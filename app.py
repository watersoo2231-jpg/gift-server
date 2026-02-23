from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import logging
from cafe24_payment import Cafe24MockClient

app = Flask(__name__)

# CORS 설정 - 모든 도메인 허용
CORS(app, 
     origins=["*"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     allow_headers=["Content-Type", "Authorization"],
     supports_credentials=True)

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cafe24 결제 클라이언트 초기화 (테스트용 Mock)
cafe24_client = Cafe24MockClient()

# 헬스 체크 엔드포인트
@app.route('/', methods=['GET'])
def health_check():
    logger.info("Health check request received")
    return jsonify({
        "status": "ok",
        "message": "Gift server is running",
        "version": "1.0.0"
    }), 200

@app.route('/api/status', methods=['GET'])
def api_status():
    logger.info("API status request received")
    return jsonify({
        "status": "connected",
        "server": "gift-server",
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }), 200

# 선물 생성 엔드포인트
@app.route('/api/gift', methods=['POST'])
def create_gift():
    try:
        data = request.get_json()
        logger.info(f"Gift creation request: {data}")
        
        # 선물 데이터 검증
        required_fields = ['recipient_email', 'product_id', 'amount']
        if not all(field in data for field in required_fields):
            return jsonify({
                "error": "Missing required fields",
                "required": required_fields
            }), 400
        
        # 선물 생성 로직
        gift_id = f"gift_{__import__('uuid').uuid4().hex[:8]}"
        
        response = {
            "success": True,
            "gift_id": gift_id,
            "recipient_email": data['recipient_email'],
            "product_id": data['product_id'],
            "amount": data['amount'],
            "status": "pending",
            "created_at": __import__('datetime').datetime.now().isoformat()
        }
        
        logger.info(f"Gift created: {gift_id}")
        return jsonify(response), 201
    
    except Exception as e:
        logger.error(f"Error creating gift: {str(e)}")
        return jsonify({
            "error": "Failed to create gift",
            "details": str(e)
        }), 500

# 선물 조회 엔드포인트
@app.route('/api/gift/<gift_id>', methods=['GET'])
def get_gift(gift_id):
    try:
        logger.info(f"Gift query request: {gift_id}")
        
        # 선물 조회 로직 (데모용)
        response = {
            "gift_id": gift_id,
            "status": "active",
            "recipient_email": "example@email.com",
            "amount": 50000,
            "created_at": __import__('datetime').datetime.now().isoformat()
        }
        
        return jsonify(response), 200
    
    except Exception as e:
        logger.error(f"Error retrieving gift: {str(e)}")
        return jsonify({
            "error": "Failed to retrieve gift",
            "details": str(e)
        }), 500

# Cafe24 결제 엔드포인트
@app.route('/api/payment/create', methods=['POST'])
def create_payment():
    try:
        data = request.get_json()
        logger.info(f"Payment creation request: {data}")
        
        # 필수 필드 검증
        required_fields = ['gift_id', 'recipient_email', 'product_id', 'amount']
        if not all(field in data for field in required_fields):
            return jsonify({
                "error": "Missing required fields",
                "required": required_fields
            }), 400
        
        # Cafe24 결제 주문 생성
        payment_result = cafe24_client.create_payment(
            gift_id=data['gift_id'],
            recipient_email=data['recipient_email'],
            product_id=data['product_id'],
            amount=data['amount'],
            product_name=data.get('product_name', '선물')
        )
        
        if payment_result['success']:
            return jsonify(payment_result), 201
        else:
            return jsonify(payment_result), 400
    
    except Exception as e:
        logger.error(f"Error creating payment: {str(e)}")
        return jsonify({
            "error": "Failed to create payment",
            "details": str(e)
        }), 500

# Cafe24 결제 검증 엔드포인트
@app.route('/api/payment/verify/<order_id>', methods=['GET'])
def verify_payment(order_id):
    try:
        logger.info(f"Payment verification request: {order_id}")
        
        # Cafe24 결제 검증
        verification_result = cafe24_client.verify_payment(order_id)
        
        if verification_result['success']:
            return jsonify(verification_result), 200
        else:
            return jsonify(verification_result), 404
    
    except Exception as e:
        logger.error(f"Error verifying payment: {str(e)}")
        return jsonify({
            "error": "Failed to verify payment",
            "details": str(e)
        }), 500

# Cafe24 결제 취소 엔드포인트
@app.route('/api/payment/cancel/<order_id>', methods=['POST'])
def cancel_payment(order_id):
    try:
        data = request.get_json() or {}
        logger.info(f"Payment cancellation request: {order_id}")
        
        # Cafe24 결제 취소
        cancellation_result = cafe24_client.cancel_payment(
            order_id=order_id,
            reason=data.get('reason', 'User request')
        )
        
        if cancellation_result['success']:
            return jsonify(cancellation_result), 200
        else:
            return jsonify(cancellation_result), 404
    
    except Exception as e:
        logger.error(f"Error cancelling payment: {str(e)}")
        return jsonify({
            "error": "Failed to cancel payment",
            "details": str(e)
        }), 500

# CORS preflight 요청 처리
@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        response = jsonify({"status": "ok"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
        response.headers.add("Access-Control-Max-Age", "3600")
        return response, 200

# 에러 핸들러
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Not found",
        "message": "The requested resource was not found"
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "Internal server error",
        "message": str(error)
    }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = port == 5000
    
    if debug_mode:
        # 로컬 개발 환경
        app.run(host='0.0.0.0', port=port, debug=True)
    else:
        # 프로덕션 환경 (gunicorn이 실행)
        app.run(host='0.0.0.0', port=port, debug=False)
