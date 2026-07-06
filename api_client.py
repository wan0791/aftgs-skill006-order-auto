# -*- coding: utf-8 -*-
"""
启小铺订单处理 — HTTP API 客户端

基于逆向的 API 端点，直接发送 HTTP 请求操作订单。
需要从浏览器导出 Cookie 后使用。

API 端点：
  - 改价:    POST /Order/cancle_order  {id, action="save", data[riseOrDrop], data[realPrice], ...}
  - 确认付款: POST /Order/comfrimPay    {id}
  - 订单列表: POST /Order/ajax_order_list
"""

import time
import random
import requests
from typing import Optional

from config import logger, send_alert, BASE_URL


class ApiClient:
    """启小铺 HTTP API 客户端"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": f"{BASE_URL}/Order/lists/type/0/status/0/",
        })

    def load_cookies_from_browser(self, page) -> bool:
        """从 Playwright 页面导出 Cookie 到 requests session"""
        try:
            playwright_cookies = page.context.cookies()
            cookie_dict = {}
            for c in playwright_cookies:
                cookie_dict[c['name']] = c['value']
            self.session.cookies.update(cookie_dict)
            logger.ok(f"已从浏览器导出 {len(cookie_dict)} 个 Cookie")
            return True
        except Exception as e:
            logger.error(f"导出 Cookie 失败：{e}")
            return False

    def modify_price(self, internal_id: str, current_price: float,
                     target_price: float, freight: float,
                     order_price: float, product: dict = None) -> bool:
        """
        通过 API 改价（原子操作）

        product 字典需包含: title, img, price, sku, num

        Returns:
            bool: 是否成功
        """
        # diff = target - orderPrice - freight
        # 不是 target - current，因为 current 已含上次调整
        diff = round(target_price - order_price - freight, 2)
        logger.info(f"API 改价 {current_price} -> {target_price} (差价 {diff})")

        p = product or {}
        p_price = p.get('price', order_price) if p.get('price', 0) > 0 else order_price

        url = f"{BASE_URL}/Order/cancle_order?v={random.randint(1, 99)}"
        data = {
            "id": internal_id,
            "action": "save",
            "data[riseOrDrop]": str(diff),
            "data[realPrice]": str(target_price),
            "data[freight]": str(freight),
            "data[orderPrice]": str(order_price),
            "data[orderid]": internal_id,
            "data[coupon][title]": "",
            "data[coupon][money]": "0",
            "data[dataset][0][title]": p.get('title', ''),
            "data[dataset][0][img]": p.get('img', ''),
            "data[dataset][0][price]": str(p_price),
            "data[dataset][0][sku]": p.get('sku', ''),
            "data[dataset][0][num]": str(p.get('num', 1)),
            "data[dataset][0][href]": "",
        }

        try:
            r = self.session.post(url, data=data, timeout=15)
            result = r.json()

            if result.get("status") == 1:
                logger.ok(f"API 改价成功: {target_price}")
                return True
            else:
                msg = result.get("msg", "未知错误")
                logger.error(f"API 改价失败: {msg}")
                return False

        except requests.RequestException as e:
            logger.error(f"API 请求异常: {e}")
            return False
        except ValueError:
            logger.error(f"API 返回非 JSON: {r.text[:200]}")
            return False

    def confirm_payment(self, internal_id: str) -> bool:
        """
        通过 API 确认付款

        Args:
            internal_id: data-id

        Returns:
            bool: 是否成功
        """
        logger.info(f"API 确认付款")

        url = f"{BASE_URL}/Order/comfrimPay?v={random.randint(1, 99)}"
        data = {"id": internal_id}

        try:
            r = self.session.post(url, data=data, timeout=15)
            result = r.json()

            if result.get("status") == 1:
                logger.ok("API 确认付款成功")
                return True
            else:
                msg = result.get("msg", "未知错误")
                logger.error(f"API 确认付款失败: {msg}")
                return False

        except requests.RequestException as e:
            logger.error(f"API 请求异常: {e}")
            return False
        except ValueError:
            logger.error(f"API 返回非 JSON: {r.text[:200]}")
            return False

    def fetch_orders(self) -> list:
        """通过 API 获取待付款订单列表"""
        url = f"{BASE_URL}/Order/ajax_order_list"
        data = {
            "type": "0", "status": "0", "dls_id": "0", "gys_id": "0",
            "tuan_id": "0", "order_ly": "0", "is_virtual": "",
            "user_group_id": "", "page": "1",
        }

        try:
            r = self.session.post(url, data=data, timeout=15)
            result = r.json()
            if result.get("status") == 1:
                orders = result.get("data", [])
                if isinstance(orders, dict):
                    orders = orders.get("list", [])
                return orders if isinstance(orders, list) else []
            return []
        except Exception as e:
            logger.error(f"获取订单列表失败: {e}")
            return []
