"""
Cafe24 결제 API 통합 모듈
"""
import requests
import json
import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class Cafe24PaymentClient:
    """Cafe24 결제 API 클라이언트"""
    
    def __init__(self, api_key: str, api_secret: str, store_id: str):
        """
        Cafe24 결제 클라이언트 초기화
        
        Args:
            api_key: Cafe24 API 키
            api_secret: Cafe24 API 시크릿
            store_id: Cafe24 스토어 ID
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.store_id = store_id
        self.base_url = "https://api.cafe24.com/v1.0"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def create_payment(self, 
                      gift_id: str,
                      recipient_email: str,
                      product_id: str,
                      amount: int,
                      product_name: str = "선물") -> Dict:
        """
        Cafe24에서 결제 주문 생성
        
        Args:
            gift_id: 선물 ID
            recipient_email: 수령자 이메일
            product_id: 상품 ID
            amount: 결제 금액 (원)
            product_name: 상품명
            
        Returns:
            결제 주문 정보
        """
        try:
            payload = {
                "store_id": self.store_id,
                "order_id": gift_id,
                "order_date": datetime.now().isoformat(),
                "customer_email": recipient_email,
                "items": [
                    {
                        "product_id": product_id,
                        "product_name": product_name,
                        "quantity": 1,
                        "price": amount
                    }
                ],
                "total_amount": amount,
                "currency": "KRW",
                "payment_method": "card"
            }
            
            response = requests.post(
                f"{self.base_url}/orders",
                headers=self.headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"Payment order created: {gift_id}")
                return {
                    "success": True,
                    "order_id": gift_id,
                    "status": "created",
                    "data": response.json()
                }
            else:
                logger.error(f"Payment creation failed: {response.text}")
                return {
                    "success": False,
                    "error": "Failed to create payment order",
                    "status_code": response.status_code
                }
                
        except Exception as e:
            logger.error(f"Error creating payment: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_payment(self, order_id: str) -> Dict:
        """
        Cafe24 결제 검증
        
        Args:
            order_id: 주문 ID
            
        Returns:
            결제 검증 결과
        """
        try:
            response = requests.get(
                f"{self.base_url}/orders/{order_id}",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Payment verified: {order_id}")
                return {
                    "success": True,
                    "order_id": order_id,
                    "status": data.get("payment_status", "unknown"),
                    "data": data
                }
            else:
                logger.error(f"Payment verification failed: {response.text}")
                return {
                    "success": False,
                    "error": "Failed to verify payment"
                }
                
        except Exception as e:
            logger.error(f"Error verifying payment: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def cancel_payment(self, order_id: str, reason: str = "User request") -> Dict:
        """
        Cafe24 결제 취소
        
        Args:
            order_id: 주문 ID
            reason: 취소 사유
            
        Returns:
            결제 취소 결과
        """
        try:
            payload = {
                "order_id": order_id,
                "reason": reason,
                "cancel_date": datetime.now().isoformat()
            }
            
            response = requests.post(
                f"{self.base_url}/orders/{order_id}/cancel",
                headers=self.headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"Payment cancelled: {order_id}")
                return {
                    "success": True,
                    "order_id": order_id,
                    "status": "cancelled",
                    "data": response.json()
                }
            else:
                logger.error(f"Payment cancellation failed: {response.text}")
                return {
                    "success": False,
                    "error": "Failed to cancel payment"
                }
                
        except Exception as e:
            logger.error(f"Error cancelling payment: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }


# 테스트용 Mock 클라이언트
class Cafe24MockClient:
    """테스트용 Mock Cafe24 클라이언트"""
    
    def __init__(self, api_key: str = "test_key", api_secret: str = "test_secret", store_id: str = "test_store"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.store_id = store_id
        self.orders = {}
    
    def create_payment(self, 
                      gift_id: str,
                      recipient_email: str,
                      product_id: str,
                      amount: int,
                      product_name: str = "선물") -> Dict:
        """Mock 결제 주문 생성"""
        order_data = {
            "order_id": gift_id,
            "customer_email": recipient_email,
            "product_id": product_id,
            "amount": amount,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }
        self.orders[gift_id] = order_data
        logger.info(f"Mock payment order created: {gift_id}")
        return {
            "success": True,
            "order_id": gift_id,
            "status": "created",
            "data": order_data
        }
    
    def verify_payment(self, order_id: str) -> Dict:
        """Mock 결제 검증"""
        if order_id in self.orders:
            order = self.orders[order_id]
            order["status"] = "completed"
            logger.info(f"Mock payment verified: {order_id}")
            return {
                "success": True,
                "order_id": order_id,
                "status": "completed",
                "data": order
            }
        return {
            "success": False,
            "error": "Order not found"
        }
    
    def cancel_payment(self, order_id: str, reason: str = "User request") -> Dict:
        """Mock 결제 취소"""
        if order_id in self.orders:
            self.orders[order_id]["status"] = "cancelled"
            self.orders[order_id]["cancelled_at"] = datetime.now().isoformat()
            self.orders[order_id]["cancel_reason"] = reason
            logger.info(f"Mock payment cancelled: {order_id}")
            return {
                "success": True,
                "order_id": order_id,
                "status": "cancelled",
                "data": self.orders[order_id]
            }
        logger.warning(f"Order not found for cancellation: {order_id}")
        return {
            "success": False,
            "error": "Order not found"
        }
