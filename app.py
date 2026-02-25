# -*- coding: utf-8 -*-
"""
선물하기 서버 (Railway 배포용)
- SQLite DB 연동
- 선물 생성/조회/결제 API
- 카페24 웹훅 수신
- 솔라피(쿨SMS) 알림톡/SMS 발송
- 배송지 입력 페이지 제공
"""

import os
import json
import hmac
import hashlib
import secrets
import logging
import base64
import time
import uuid
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g
from flask_cors import CORS
import requests

# ──────────────────────────────────────────────
# 설정값 (환경변수로 관리)
# ──────────────────────────────────────────────
CAFE24_MALL_ID        = os.getenv("CAFE24_MALL_ID",        "pathocrionwater")
CAFE24_CLIENT_ID      = os.getenv("CAFE24_CLIENT_ID",      "NiqeMRujVKFU1zE5OWzUID")
CAFE24_CLIENT_SECRET  = os.getenv("CAFE24_CLIENT_SECRET",  "Yb3GU2KIKZltbQZrgojV1C")
CAFE24_WEBHOOK_SECRET = os.getenv("CAFE24_WEBHOOK_SECRET", "")

# 솔라피(쿨SMS) 설정 - 발송번호 070-4101-9003
COOLSMS_API_KEY    = os.getenv("COOLSMS_API_KEY",    "")
COOLSMS_API_SECRET = os.getenv("COOLSMS_API_SECRET", "")
COOLSMS_SENDER     = os.getenv("COOLSMS_SENDER",     "07041019003")  # 발송번호 070-4101-9003
KAKAO_CHANNEL_ID   = os.getenv("KAKAO_CHANNEL_ID",   "")
KAKAO_TEMPLATE_ID  = os.getenv("KAKAO_TEMPLATE_ID",  "")

SERVER_URL       = os.getenv("SERVER_URL", "https://gift-server-production-3f51.up.railway.app")
GIFT_EXPIRE_DAYS = int(os.getenv("GIFT_EXPIRE_DAYS", "7"))
DATABASE         = os.getenv("DATABASE", "gifts.db")

# Railway 환경변수 자동 저장용
RAILWAY_TOKEN          = os.getenv("RAILWAY_TOKEN", "")
RAILWAY_SERVICE_ID     = os.getenv("RAILWAY_SERVICE_ID", "")
RAILWAY_ENVIRONMENT_ID = os.getenv("RAILWAY_ENVIRONMENT_ID", "")

# 카페24 토큰 캐시
_token_cache = {"token": None, "expires_at": 0}
_refresh_token_store = {"token": os.getenv("CAFE24_REFRESH_TOKEN", "")}

# ──────────────────────────────────────────────
# Flask 앱 초기화
# ──────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"], "allow_headers": ["*"]}})

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 데이터베이스
# ──────────────────────────────────────────────
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """DB 테이블 초기화"""
    with app.app_context():
        db = sqlite3.connect(DATABASE)
        db.execute('''
            CREATE TABLE IF NOT EXISTS gifts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                gift_id      TEXT UNIQUE NOT NULL,
                order_id     TEXT,
                recipient_email TEXT NOT NULL,
                recipient_phone TEXT,
                product_id   TEXT NOT NULL,
                product_name TEXT,
                amount       INTEGER NOT NULL,
                sender_name  TEXT,
                gift_message TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now')),
                address_token TEXT,
                expire_at    TEXT,
                recv_name    TEXT,
                recv_phone   TEXT,
                recv_zip     TEXT,
                recv_addr1   TEXT,
                recv_addr2   TEXT,
                recv_memo    TEXT
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   TEXT UNIQUE NOT NULL,
                gift_id    TEXT,
                amount     INTEGER NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending',
                customer_email TEXT,
                product_id TEXT,
                product_name TEXT,
                cancel_reason TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        db.commit()
        db.close()
        logger.info("DB 초기화 완료")

# 서버 시작 시 DB 초기화
init_db()


# ──────────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────────
def verify_cafe24_webhook(req) -> bool:
    if not CAFE24_WEBHOOK_SECRET:
        return True
    signature = req.headers.get("X-Cafe24-Signature", "")
    body = req.get_data()
    expected = hmac.new(CAFE24_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_coolsms_auth_header() -> str:
    date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = secrets.token_hex(16)
    data = date + salt
    signature = hmac.new(COOLSMS_API_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f'HMAC-SHA256 apiKey={COOLSMS_API_KEY}, date={date}, salt={salt}, signature={signature}'


def send_alimtalk(receiver_phone, receiver_name, sender_name, gift_message, order_id, token, expire_date) -> bool:
    """솔라피(쿨SMS) API로 카카오 알림톡 발송 - 발신번호: 070-4101-9003"""
    if not COOLSMS_API_KEY or not COOLSMS_API_SECRET:
        logger.warning("솔라피 API 키 미설정 - 알림톡 발송 건너뜀")
        return False

    address_url = (
        f"{SERVER_URL}/gift/address"
        f"?token={token}"
        f"&order_id={requests.utils.quote(order_id)}"
        f"&from={requests.utils.quote(sender_name)}"
        f"&msg={requests.utils.quote(gift_message or '')}"
        f"&expire={requests.utils.quote(expire_date)}"
    )

    try:
        payload = {
            "message": {
                "to": receiver_phone.replace("-", ""),
                "from": COOLSMS_SENDER.replace("-", ""),  # 070-4101-9003
                "type": "ATA",
                "kakaoOptions": {
                    "pfId": KAKAO_CHANNEL_ID,
                    "templateId": KAKAO_TEMPLATE_ID,
                    "variables": {
                        "#{받는분이름}": receiver_name or "고객",
                        "#{보내는분이름}": sender_name or "고객",
                        "#{만료일}": expire_date,
                        "#{선물메시지}": gift_message or "마음을 담아 보낸 선물입니다.",
                        "#{배송지입력링크}": address_url
                    },
                    "buttons": [{"buttonType": "WL", "buttonName": "배송지 입력하기", "linkMo": address_url, "linkPc": address_url}]
                }
            }
        }
        resp = requests.post(
            "https://api.coolsms.co.kr/messages/v4/send",
            headers={"Authorization": get_coolsms_auth_header(), "Content-Type": "application/json"},
            json=payload, timeout=10
        )
        if resp.status_code in (200, 201):
            logger.info(f"알림톡 발송 성공: {receiver_phone}")
            return True
        else:
            logger.error(f"알림톡 발송 실패: {resp.status_code} {resp.text[:300]}")
            return send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, address_url, expire_date)
    except Exception as e:
        logger.error(f"알림톡 발송 오류: {e}")
        return send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, address_url, expire_date)


def send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, address_url, expire_date) -> bool:
    """알림톡 실패 시 SMS 대체 발송 - 발신번호: 070-4101-9003"""
    if not COOLSMS_API_KEY:
        return False
    try:
        text = (
            f"[선물 도착] {receiver_name}님,\n"
            f"{sender_name}님이 선물을 보내셨어요!\n"
            f"아래 링크에서 배송지를 입력해 주세요.\n"
            f"{address_url}\n"
            f"※ {expire_date}까지 입력 필요"
        )
        payload = {
            "message": {
                "to": receiver_phone.replace("-", ""),
                "from": COOLSMS_SENDER.replace("-", ""),  # 070-4101-9003
                "type": "LMS",
                "text": text
            }
        }
        resp = requests.post(
            "https://api.coolsms.co.kr/messages/v4/send",
            headers={"Authorization": get_coolsms_auth_header(), "Content-Type": "application/json"},
            json=payload, timeout=10
        )
        if resp.status_code in (200, 201):
            logger.info(f"SMS 대체 발송 성공: {receiver_phone}")
            return True
        else:
            logger.error(f"SMS 대체 발송 실패: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"SMS 대체 발송 오류: {e}")
        return False


def save_refresh_token_to_railway(refresh_token: str):
    if not RAILWAY_TOKEN or not RAILWAY_SERVICE_ID or not RAILWAY_ENVIRONMENT_ID:
        return
    try:
        query = """mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
          variableCollectionUpsert(input: $input)
        }"""
        variables = {"input": {"serviceId": RAILWAY_SERVICE_ID, "environmentId": RAILWAY_ENVIRONMENT_ID, "variables": {"CAFE24_REFRESH_TOKEN": refresh_token}}}
        resp = requests.post("https://backboard.railway.app/graphql/v2",
            headers={"Authorization": f"Bearer {RAILWAY_TOKEN}", "Content-Type": "application/json"},
            json={"query": query, "variables": variables}, timeout=10)
        if resp.status_code == 200 and "errors" not in resp.json():
            logger.info("Railway 환경변수에 리프레시 토큰 저장 완료")
    except Exception as e:
        logger.error(f"Railway 환경변수 저장 오류: {e}")


def get_cafe24_access_token() -> str:
    global _token_cache, _refresh_token_store
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    refresh_token = _refresh_token_store.get("token", "")
    if not refresh_token:
        logger.error("리프레시 토큰 없음 - /oauth/install 로 OAuth 인증 필요")
        return None
    try:
        credentials = base64.b64encode(f"{CAFE24_CLIENT_ID}:{CAFE24_CLIENT_SECRET}".encode()).decode()
        resp = requests.post(
            f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/token",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _token_cache["token"] = data.get("access_token")
            _token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
            new_refresh = data.get("refresh_token")
            if new_refresh:
                _refresh_token_store["token"] = new_refresh
                save_refresh_token_to_railway(new_refresh)
            return _token_cache["token"]
        else:
            logger.error(f"토큰 갱신 실패: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"토큰 요청 오류: {e}")
        return None


def update_cafe24_order_address(order_id, name, phone, zipcode, address1, address2, memo):
    try:
        access_token = get_cafe24_access_token()
        if not access_token:
            return False
        url = f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/admin/orders/{order_id}/shippingaddress"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "X-Cafe24-Api-Version": "2024-06-01"}
        payload = {"shop_no": 1, "shipping_address": {"name": name, "phone": phone.replace("-", ""), "cellphone": phone.replace("-", ""), "zipcode": zipcode, "address1": address1, "address2": address2, "shipping_message": memo}}
        resp = requests.put(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            logger.info(f"카페24 배송지 업데이트 성공: {order_id}")
            return True
        else:
            logger.error(f"카페24 배송지 업데이트 실패: {resp.status_code} {resp.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"카페24 API 호출 오류: {e}")
        return False


# ──────────────────────────────────────────────
# 라우트
# ──────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "service": "gift-server", "version": "2.0.0"}), 200


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"status": "connected", "server": "gift-server", "timestamp": datetime.now().isoformat()}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat(), "oauth_ready": bool(_refresh_token_store.get("token"))}), 200


# ── 선물 생성 ──
@app.route("/api/gift", methods=["POST"])
def create_gift():
    try:
        data = request.get_json()
        # recipient_phone 또는 recipient_email 중 하나만 있어도 OK
        if not data:
            return jsonify({"error": "Missing request body"}), 400
        if not data.get('recipient_phone') and not data.get('recipient_email'):
            return jsonify({"error": "recipient_phone 또는 recipient_email 중 하나는 필수입니다."}), 400
        if not data.get('product_id'):
            return jsonify({"error": "product_id는 필수입니다."}), 400

        # amount가 없거나 0이면 0으로 처리 (카페24 변수 치환 실패 대비)
        try:
            amount = int(data.get('amount', 0))
        except (ValueError, TypeError):
            amount = 0

        # recipient_email이 없으면 전화번호 기반으로 생성
        recipient_phone = data.get('recipient_phone', '').replace('-', '')
        recipient_email = data.get('recipient_email') or f"{recipient_phone}@gift.local"

        gift_id = f"gift_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        expire_at = (datetime.now() + timedelta(days=GIFT_EXPIRE_DAYS)).isoformat()
        address_token = secrets.token_urlsafe(32)

        db = get_db()
        db.execute(
            "INSERT INTO gifts (gift_id, recipient_email, recipient_phone, product_id, product_name, amount, sender_name, gift_message, status, created_at, updated_at, address_token, expire_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gift_id, recipient_email, recipient_phone, data['product_id'], data.get('product_name', '선물'), amount, data.get('sender_name', ''), data.get('gift_message', ''), 'pending', now, now, address_token, expire_at)
        )
        db.commit()

        # 알림톡/SMS 발송 (전화번호가 있을 경우)
        if recipient_phone:
            expire_str = (datetime.now() + timedelta(days=GIFT_EXPIRE_DAYS)).strftime("%Y년 %m월 %d일")
            send_alimtalk(
                receiver_phone=recipient_phone,
                receiver_name=data.get('recipient_name', '고객'),
                sender_name=data.get('sender_name', '고객'),
                gift_message=data.get('gift_message', ''),
                order_id=gift_id,
                token=address_token,
                expire_date=expire_str
            )

        logger.info(f"Gift created: {gift_id}")
        return jsonify({"success": True, "gift_id": gift_id, "recipient_email": recipient_email, "product_id": data['product_id'], "amount": amount, "status": "pending", "created_at": now}), 201

    except Exception as e:
        logger.error(f"Error creating gift: {e}")
        return jsonify({"error": "Failed to create gift", "details": str(e)}), 500


# ── 선물 조회 ──
@app.route("/api/gift/<gift_id>", methods=["GET"])
def get_gift(gift_id):
    try:
        db = get_db()
        gift = db.execute("SELECT * FROM gifts WHERE gift_id = ?", (gift_id,)).fetchone()
        if not gift:
            return jsonify({"error": "Gift not found"}), 404
        return jsonify(dict(gift)), 200
    except Exception as e:
        logger.error(f"Error retrieving gift: {e}")
        return jsonify({"error": "Failed to retrieve gift", "details": str(e)}), 500


# ── 결제 생성 ──
@app.route("/api/payment/create", methods=["POST"])
def create_payment():
    try:
        data = request.get_json()
        if not data or not data.get('gift_id'):
            return jsonify({"error": "gift_id는 필수입니다."}), 400

        try:
            amount = int(data.get('amount', 0))
        except (ValueError, TypeError):
            amount = 0

        order_id = data['gift_id']
        customer_email = data.get('recipient_email') or f"{data.get('recipient_phone','unknown')}@gift.local"
        now = datetime.now().isoformat()

        db = get_db()
        existing = db.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
        if existing:
            return jsonify({"success": True, "order_id": order_id, "status": existing['status'], "data": dict(existing)}), 200

        db.execute(
            "INSERT INTO payments (order_id, gift_id, amount, status, customer_email, product_id, product_name, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (order_id, data['gift_id'], amount, 'pending', customer_email, data.get('product_id',''), data.get('product_name', '선물'), now, now)
        )
        db.commit()

        payment = db.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
        return jsonify({"success": True, "order_id": order_id, "status": "created", "data": dict(payment)}), 201

    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        return jsonify({"error": "Failed to create payment", "details": str(e)}), 500


# ── 결제 검증 ──
@app.route("/api/payment/verify/<order_id>", methods=["GET"])
def verify_payment(order_id):
    try:
        db = get_db()
        payment = db.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
        if not payment:
            return jsonify({"success": False, "error": "Order not found"}), 404

        # 결제 완료 처리 (실제 환경에서는 카페24 API로 검증)
        db.execute("UPDATE payments SET status = 'completed', updated_at = ? WHERE order_id = ?", (datetime.now().isoformat(), order_id))
        db.commit()
        payment = db.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
        return jsonify({"success": True, "order_id": order_id, "status": payment['status'], "data": dict(payment)}), 200

    except Exception as e:
        logger.error(f"Error verifying payment: {e}")
        return jsonify({"error": "Failed to verify payment", "details": str(e)}), 500


# ── 결제 취소 ──
@app.route("/api/payment/cancel/<order_id>", methods=["POST"])
def cancel_payment(order_id):
    try:
        data = request.get_json() or {}
        db = get_db()
        payment = db.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
        if not payment:
            return jsonify({"success": False, "error": "Order not found"}), 404

        reason = data.get('reason', 'User request')
        db.execute("UPDATE payments SET status = 'cancelled', cancel_reason = ?, updated_at = ? WHERE order_id = ?", (reason, datetime.now().isoformat(), order_id))
        db.commit()
        return jsonify({"success": True, "order_id": order_id, "status": "cancelled", "cancel_reason": reason}), 200

    except Exception as e:
        logger.error(f"Error cancelling payment: {e}")
        return jsonify({"error": "Failed to cancel payment", "details": str(e)}), 500


# ── 카페24 웹훅 ──
@app.route("/webhook/cafe24/order", methods=["POST"])
def cafe24_order_webhook():
    if not verify_cafe24_webhook(request):
        return jsonify({"error": "Invalid signature"}), 401
    try:
        data = request.get_json(force=True)
        logger.info(f"웹훅 수신: {json.dumps(data, ensure_ascii=False)[:300]}")
    except Exception as e:
        return jsonify({"error": "Invalid JSON"}), 400

    resource = data.get("resource", {})
    order_id       = resource.get("order_id") or data.get("order_id", "")
    is_gift        = resource.get("is_gift", "0")
    order_memo     = resource.get("order_memo", "") or ""
    receiver_name  = resource.get("gift_receiver_name", "")
    receiver_phone = resource.get("gift_receiver_phone", "")
    gift_message   = resource.get("gift_message", "")
    sender_name    = resource.get("buyer_name", "") or resource.get("member_id", "고객")

    if str(is_gift) != "1" and "선물주문" not in order_memo:
        return jsonify({"status": "not_gift_order"}), 200
    if not receiver_phone:
        return jsonify({"status": "no_receiver_phone"}), 200

    address_token = secrets.token_urlsafe(32)
    expire_at = datetime.now() + timedelta(days=GIFT_EXPIRE_DAYS)
    expire_date = expire_at.strftime("%Y년 %m월 %d일")

    db = get_db()
    gift_id = f"gift_{uuid.uuid4().hex[:12]}"
    db.execute(
        "INSERT OR REPLACE INTO gifts (gift_id, order_id, recipient_email, recipient_phone, product_id, amount, sender_name, gift_message, status, address_token, expire_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (gift_id, order_id, '', receiver_phone, 'CAFE24', 0, sender_name, gift_message, 'pending', address_token, expire_at.isoformat())
    )
    db.commit()

    success = send_alimtalk(receiver_phone=receiver_phone, receiver_name=receiver_name or "고객",
        sender_name=sender_name, gift_message=gift_message, order_id=order_id,
        token=address_token, expire_date=expire_date)

    return jsonify({"status": "success" if success else "send_failed", "order_id": order_id}), 200


# ── 배송지 입력 페이지 ──
@app.route("/gift/address", methods=["GET"])
def gift_address_page():
    return ADDRESS_PAGE_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route("/gift/address", methods=["POST"])
def save_gift_address():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"success": False, "message": "잘못된 요청입니다."}), 400

    token    = data.get("token", "")
    order_id = data.get("order_id", "")

    db = get_db()
    gift = db.execute("SELECT * FROM gifts WHERE address_token = ?", (token,)).fetchone()
    if not gift:
        return jsonify({"success": False, "message": "유효하지 않은 링크입니다."}), 400
    if gift['status'] == 'address_submitted':
        return jsonify({"success": False, "message": "이미 배송지가 입력된 선물입니다."}), 400
    try:
        expire_dt = datetime.fromisoformat(gift['expire_at'])
        if datetime.now() > expire_dt:
            return jsonify({"success": False, "message": "배송지 입력 기한이 지났습니다."}), 400
    except Exception:
        pass

    recv_name  = data.get("recv_name", "").strip()
    recv_phone = data.get("recv_phone", "").strip()
    recv_zip   = data.get("recv_zip", "").strip()
    recv_addr1 = data.get("recv_addr1", "").strip()
    recv_addr2 = data.get("recv_addr2", "").strip()
    recv_memo  = data.get("recv_memo", "").strip()

    if not all([recv_name, recv_phone, recv_zip, recv_addr1]):
        return jsonify({"success": False, "message": "이름, 연락처, 주소를 모두 입력해 주세요."}), 400

    success = update_cafe24_order_address(order_id=order_id, name=recv_name, phone=recv_phone, zipcode=recv_zip, address1=recv_addr1, address2=recv_addr2, memo=recv_memo)

    db.execute(
        "UPDATE gifts SET status='address_submitted', recv_name=?, recv_phone=?, recv_zip=?, recv_addr1=?, recv_addr2=?, recv_memo=?, updated_at=? WHERE address_token=?",
        (recv_name, recv_phone, recv_zip, recv_addr1, recv_addr2, recv_memo, datetime.now().isoformat(), token)
    )
    db.commit()

    logger.info(f"배송지 등록 완료: 주문번호={order_id}")
    return jsonify({"success": True})


# ── OAuth ──
@app.route("/oauth/install", methods=["GET"])
def oauth_install():
    import urllib.parse
    scope = "mall.read_order,mall.write_order"
    state = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({"response_type": "code", "client_id": CAFE24_CLIENT_ID, "redirect_uri": f"{SERVER_URL}/oauth/callback", "scope": scope, "state": state})
    auth_url = f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/authorize?{params}"
    return f"""<html><head><meta charset='UTF-8'></head><body>
    <h2>카페24 OAuth 인증</h2>
    <a href="{auth_url}" style="display:inline-block;padding:14px 28px;background:#222;color:#fff;text-decoration:none;border-radius:8px;font-size:16px;margin-top:20px">카페24 관리자 로그인으로 인증하기</a>
    </body></html>"""


@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    code = request.args.get("code", "")
    error = request.args.get("error", "")
    if error:
        return f"<h2>인증 실패: {error}</h2>", 400
    if not code:
        return "<h2>인증 코드가 없습니다.</h2>", 400
    try:
        credentials = base64.b64encode(f"{CAFE24_CLIENT_ID}:{CAFE24_CLIENT_SECRET}".encode()).decode()
        resp = requests.post(
            f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/token",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": f"{SERVER_URL}/oauth/callback"}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _token_cache["token"] = data.get("access_token")
            _token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
            refresh_token = data.get("refresh_token", "")
            if refresh_token:
                _refresh_token_store["token"] = refresh_token
                save_refresh_token_to_railway(refresh_token)
            return """<html><head><meta charset='UTF-8'></head><body>
            <h2 style='color:green'>✅ OAuth 인증 완료!</h2>
            <p>카페24 주문 API 연동이 완료되었습니다.</p>
            </body></html>"""
        else:
            return f"<h2>토큰 교환 실패: {resp.status_code}</h2>", 400
    except Exception as e:
        return f"<h2>오류 발생: {e}</h2>", 500


# ── 관리자 ──
@app.route("/admin/gifts", methods=["GET"])
def admin_gifts():
    db = get_db()
    gifts = db.execute("SELECT * FROM gifts ORDER BY created_at DESC").fetchall()
    rows = "".join(f"<tr><td>{g['gift_id']}</td><td>{g['order_id'] or '-'}</td><td>{g['recipient_email']}</td><td>{g['amount']}</td><td>{'✅ 완료' if g['status'] == 'address_submitted' else '⏳ 대기'}</td><td>{g['created_at']}</td></tr>" for g in gifts)
    return f"""<html><head><meta charset='UTF-8'><title>선물 주문 관리</title>
    <style>body{{font-family:sans-serif;padding:20px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;font-size:13px}}</style></head>
    <body><h2>🎁 선물 주문 현황 (총 {len(gifts)}건)</h2>
    <table><tr><th>선물ID</th><th>주문번호</th><th>받는분이메일</th><th>금액</th><th>상태</th><th>생성일시</th></tr>{rows}</table></body></html>"""


# ── 에러 핸들러 ──
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error", "message": str(error)}), 500

@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        response = jsonify({"status": "ok"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
        return response, 200


# ──────────────────────────────────────────────
# 배송지 입력 페이지 HTML
# ──────────────────────────────────────────────
ADDRESS_PAGE_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>선물 배송지 입력</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; background: #f8f8f8; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .wrap { background: #fff; border-radius: 16px; padding: 36px 28px; max-width: 420px; width: 100%; box-shadow: 0 4px 24px rgba(0,0,0,0.08); margin: 20px; }
  .gift-icon { font-size: 48px; text-align: center; margin-bottom: 12px; }
  h2 { text-align: center; font-size: 20px; color: #222; margin-bottom: 6px; }
  .sender { text-align: center; color: #888; font-size: 14px; margin-bottom: 24px; }
  .msg-box { background: #fff9e6; border-left: 4px solid #f5c518; padding: 12px 16px; border-radius: 8px; font-size: 14px; color: #555; margin-bottom: 24px; }
  label { display: block; font-size: 13px; color: #555; margin-bottom: 6px; font-weight: 600; }
  input { width: 100%; padding: 12px 14px; border: 1px solid #ddd; border-radius: 8px; font-size: 15px; margin-bottom: 16px; outline: none; }
  input:focus { border-color: #222; }
  .addr-row { display: flex; gap: 8px; margin-bottom: 8px; }
  .addr-row input { margin-bottom: 0; flex: 1; }
  .addr-btn { padding: 12px 14px; background: #222; color: #fff; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; white-space: nowrap; }
  .submit-btn { width: 100%; padding: 15px; background: #222; color: #fff; border: none; border-radius: 10px; font-size: 16px; font-weight: 700; cursor: pointer; margin-top: 8px; }
  .submit-btn:hover { background: #444; }
  .expire { text-align: center; color: #aaa; font-size: 12px; margin-top: 16px; }
  .result { text-align: center; padding: 40px 20px; }
</style>
</head>
<body>
<div class="wrap" id="app">
  <div class="gift-icon">🎁</div>
  <p style="text-align:center;color:#aaa;">로딩 중...</p>
</div>
<script src="//t1.daumcdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js"></script>
<script>
const params = new URLSearchParams(location.search);
const token = params.get('token') || '';
const orderId = params.get('order_id') || '';
const sender = decodeURIComponent(params.get('from') || '');
const msg = decodeURIComponent(params.get('msg') || '');
const expire = decodeURIComponent(params.get('expire') || '');
const app = document.getElementById('app');
app.innerHTML = `
  <div class="gift-icon">🎁</div>
  <h2>${sender ? sender + '님이 선물을 보냈어요!' : '선물이 도착했어요!'}</h2>
  <p class="sender">배송지를 입력하시면 선물을 받으실 수 있습니다</p>
  ${msg ? '<div class="msg-box">💌 ' + msg + '</div>' : ''}
  <label>받는 분 이름 <span style="color:red">*</span></label>
  <input type="text" id="name" placeholder="이름을 입력하세요">
  <label>연락처 <span style="color:red">*</span></label>
  <input type="tel" id="phone" placeholder="010-0000-0000">
  <label>우편번호 <span style="color:red">*</span></label>
  <div class="addr-row">
    <input type="text" id="zipcode" placeholder="우편번호" readonly>
    <button type="button" class="addr-btn" onclick="searchAddr()">주소 검색</button>
  </div>
  <input type="text" id="address1" placeholder="기본 주소" readonly style="margin-top:0">
  <input type="text" id="address2" placeholder="상세 주소 (동/호수 등)">
  <input type="text" id="memo" placeholder="배송 메모 (선택)">
  <button class="submit-btn" onclick="submitAddr()">배송지 등록하기</button>
  ${expire ? '<p class="expire">⏰ 입력 기한: ' + expire + '</p>' : ''}
`;
function searchAddr() {
  new daum.Postcode({
    oncomplete: function(data) {
      document.getElementById('zipcode').value = data.zonecode;
      document.getElementById('address1').value = data.roadAddress || data.jibunAddress;
    }
  }).open();
}
async function submitAddr() {
  const name = document.getElementById('name').value.trim();
  const phone = document.getElementById('phone').value.trim();
  const zipcode = document.getElementById('zipcode').value.trim();
  const address1 = document.getElementById('address1').value.trim();
  const address2 = document.getElementById('address2').value.trim();
  const memo = document.getElementById('memo').value.trim();
  if (!name || !phone || !zipcode || !address1) {
    alert('이름, 연락처, 주소를 모두 입력해 주세요.');
    return;
  }
  const btn = document.querySelector('.submit-btn');
  btn.disabled = true;
  btn.textContent = '등록 중...';
  try {
    const resp = await fetch('/gift/address', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token, order_id: orderId, recv_name: name, recv_phone: phone, recv_zip: zipcode, recv_addr1: address1, recv_addr2: address2, recv_memo: memo})
    });
    const result = await resp.json();
    if (result.success) {
      app.innerHTML = '<div class="result"><div style="font-size:60px;margin-bottom:16px">🎉</div><h2 style="color:#2ecc71;margin-bottom:12px">배송지가 등록되었습니다!</h2><p style="color:#888;font-size:14px;line-height:1.6">선물이 곧 배송될 예정입니다.<br>감사합니다!</p></div>';
    } else {
      alert(result.message || '오류가 발생했습니다.');
      btn.disabled = false;
      btn.textContent = '배송지 등록하기';
    }
  } catch(e) {
    alert('네트워크 오류가 발생했습니다. 다시 시도해 주세요.');
    btn.disabled = false;
    btn.textContent = '배송지 등록하기';
  }
}
</script>
</body>
</html>"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=(port == 5000))
