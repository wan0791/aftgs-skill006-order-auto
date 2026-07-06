# -*- coding: utf-8 -*-
"""
启小铺订单处理 — 留言解析器

解析买家留言，判断处理类型：
- skip: 跳过（无有效指令关键词）
- modify_confirm: 改价 + 确认付款
- modify_skip: 改价 + 备用金（跳过确认）
- confirm: 直接确认付款

逻辑与技能004（微分销）完全一致，只认 4 个白名单关键词。
"""

import re
from typing import Optional
from dataclasses import dataclass
from enum import Enum
from config import logger


class OrderType(Enum):
    """订单类型"""
    SKIP = 1           # 跳过
    MODIFY_CONFIRM = 2 # 改价 + 确认
    MODIFY_SKIP = 3    # 改价 + 跳过确认（备用金）
    CONFIRM_ONLY = 4   # 直接确认


@dataclass
class OrderInfo:
    """订单信息"""
    order_id: str
    message: str
    current_price: float
    order_type: OrderType
    internal_id: str = ""          # data-id（内部订单ID）
    target_price: Optional[float] = None
    price_diff: Optional[float] = None


class MessageParser:
    """留言解析器 — 白名单匹配 + 噪音忽略 + 保守跳过"""

    def parse(self, message: str, current_price: float, order_id: str,
              internal_id: str = "") -> OrderInfo:
        """
        解析订单留言，确定处理类型

        只认 4 个指令关键词："改价"、"备用金"、"直接确认"、"确认付款"
        其余内容（装车、叫车、货品价格、财务信息等）一律视为噪音
        无法匹配任何指令 → 保守跳过
        """
        message = message.strip() if message else ""
        raw_msg = message  # 保留原始留言用于日志

        # 类型 1：无留言 / "留言"二字 / 图标
        if message == "留言" or message == "留言：" or message == "":
            logger.info(f"  [解析] {order_id}: 空留言 → SKIP")
            return OrderInfo(
                order_id=order_id,
                message=message,
                current_price=current_price,
                order_type=OrderType.SKIP,
                internal_id=internal_id,
                target_price=None,
                price_diff=None
            )

        # 清理图标相关文本
        if any(c in message for c in ['📱', '💬', '📞', '微信', '图标']):
            cleaned = re.sub(r'[📱💬📞]|微信|图标', '', message).strip()
            if cleaned == "留言" or cleaned == "":
                logger.info(f"  [解析] {order_id}: 图标留言 → SKIP")
                return OrderInfo(
                    order_id=order_id,
                    message=message,
                    current_price=current_price,
                    order_type=OrderType.SKIP,
                    internal_id=internal_id,
                    target_price=None,
                    price_diff=None
                )
            message = cleaned

        # 类型 3/4：备用金订单
        if "备用金" in message:
            target_price = self._extract_price(message)
            if target_price:
                if abs(current_price - target_price) <= 0.01:
                    logger.info(f"  [解析] {order_id}: 备用金 ¥{target_price} 已匹配(当前¥{current_price}) → SKIP")
                    return OrderInfo(
                        order_id=order_id,
                        message=message,
                        current_price=current_price,
                        order_type=OrderType.SKIP,
                        internal_id=internal_id,
                        target_price=target_price,
                        price_diff=0.0
                    )
                else:
                    logger.info(f"  [解析] {order_id}: 备用金 ¥{target_price} (当前¥{current_price}) → MODIFY_SKIP")
                    return OrderInfo(
                        order_id=order_id,
                        message=message,
                        current_price=current_price,
                        order_type=OrderType.MODIFY_SKIP,
                        internal_id=internal_id,
                        target_price=target_price,
                        price_diff=target_price - current_price
                    )
            else:
                logger.warn(f"  [解析] {order_id}: 含'备用金'但未提取到金额，留言=\"{raw_msg}\" → SKIP")
                return OrderInfo(
                    order_id=order_id,
                    message=message,
                    current_price=current_price,
                    order_type=OrderType.SKIP,
                    internal_id=internal_id,
                    target_price=None,
                    price_diff=None
                )

        # 类型 2/2a：改价订单（不含备用金）
        if "改价" in message:
            target_price = self._extract_price(message)
            if target_price:
                if abs(current_price - target_price) <= 0.01:
                    logger.info(f"  [解析] {order_id}: 改价 ¥{target_price} 已匹配(当前¥{current_price}) → SKIP")
                    return OrderInfo(
                        order_id=order_id,
                        message=message,
                        current_price=current_price,
                        order_type=OrderType.SKIP,
                        internal_id=internal_id,
                        target_price=target_price,
                        price_diff=0.0
                    )
                else:
                    logger.info(f"  [解析] {order_id}: 改价 ¥{target_price} (当前¥{current_price}) → MODIFY_CONFIRM")
                    return OrderInfo(
                        order_id=order_id,
                        message=message,
                        current_price=current_price,
                        order_type=OrderType.MODIFY_CONFIRM,
                        internal_id=internal_id,
                        target_price=target_price,
                        price_diff=target_price - current_price
                    )
            else:
                logger.warn(f"  [解析] {order_id}: 含'改价'但未提取到金额，留言=\"{raw_msg}\" → SKIP")
                return OrderInfo(
                    order_id=order_id,
                    message=message,
                    current_price=current_price,
                    order_type=OrderType.SKIP,
                    internal_id=internal_id,
                    target_price=None,
                    price_diff=None
                )

        # 类型 5：直接确认 / 确认付款
        if "直接确认" in message or "确认付款" in message:
            logger.info(f"  [解析] {order_id}: 直接确认/确认付款 → CONFIRM_ONLY")
            return OrderInfo(
                order_id=order_id,
                message=message,
                current_price=current_price,
                order_type=OrderType.CONFIRM_ONLY,
                internal_id=internal_id
            )

        # 类型 0：无法匹配任何指令关键词 → 保守跳过
        logger.info(f"  [解析] {order_id}: 无关键词匹配，留言=\"{raw_msg[:60]}\" → SKIP")
        return OrderInfo(
            order_id=order_id,
            message=message,
            current_price=current_price,
            order_type=OrderType.SKIP,
            internal_id=internal_id,
            target_price=None,
            price_diff=None
        )

    def _extract_price(self, message: str) -> Optional[float]:
        """
        从留言中提取价格

        规则：
        1. 优先匹配 "改价[改] XXX 元" 或 "改价[改] XXX"（支持"改价改XXX"口语化表达）
        2. 匹配 "备用金 XXX 元" 或 "备用金 XXX"
        3. 匹配其他 "XXX 元" 格式
        """
        patterns = [
            r'改价(?:改)?\s*(\d+(?:\.\d+)?)\s*元',
            r'改价(?:改)?\s*(\d+(?:\.\d+)?)',
            r'备用金\s*(\d+(?:\.\d+)?)\s*元',
            r'备用金\s*(\d+(?:\.\d+)?)',
        ]

        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue

        # 兜底：匹配 "XXX 元"
        match = re.search(r'(\d+(?:\.\d+)?)\s*元', message)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass

        return None
