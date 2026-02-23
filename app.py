"""
선물하기 웹훅 서버
====================
카페24 주문 완료 웹훅을 수신하여
선물 받는 사람에게 카카오 알림톡으로 배송지 입력 링크를 자동 발송합니다.

배포 시:
  gunicorn -w 2 -b 0.0.0.0:$PORT app:app
"""

import os
import json
import hmac
import hashlib
import secrets
import logging
import base64
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
from flask_cors import CORS

# ──────────────────────────────────────────────
# 설정값 (환경변수로 관리)
# ──────────────────────────────────────────────
CAFE24_MALL_ID       = os.getenv("CAFE24_MALL_ID",       "pathocrionwater")
CAFE24_CLIENT_ID     = os.getenv("CAFE24_CLIENT_ID",     "NiqeMRujVKFU1zE5OWzUID")
CAFE24_CLIENT_SECRET = os.getenv("CAFE24_CLIENT_SECRET", "Yb3GU2KIKZltbQZrgojV1C")
CAFE24_WEBHOOK_SECRET= os.getenv("CAFE24_WEBHOOK_SECRET","")

# 쿨SMS HTTP API (SDK 없이 직접 호출)
COOLSMS_API_KEY    = os.getenv("COOLSMS_API_KEY",    "")
COOLSMS_API_SECRET = os.getenv("COOLSMS_API_SECRET", "")
COOLSMS_SENDER     = os.getenv("COOLSMS_SENDER",     "")
KAKAO_CHANNEL_ID   = os.getenv("KAKAO_CHANNEL_ID",   "")
KAKAO_TEMPLATE_ID  = os.getenv("KAKAO_TEMPLATE_ID",  "")

# 배송지 입력 페이지 URL (서버 자체 제공)
SERVER_URL         = os.getenv("SERVER_URL", "https://gift-server-tuiy.onrender.com")
GIFT_ADDRESS_URL   = f"{SERVER_URL}/gift/address"

GIFT_EXPIRE_DAYS   = int(os.getenv("GIFT_EXPIRE_DAYS", "7"))

# ──────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 인메모리 토큰 저장소 (운영 시 DB/Redis 권장)
gift_tokens = {}

# 카페24 액세스 토큰 캐시
_token_cache = {"token": None, "expires_at": 0}


# ──────────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────────

def generate_token(order_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expire = datetime.now() + timedelta(days=GIFT_EXPIRE_DAYS)
    gift_tokens[token] = {
        "order_id": order_id,
        "expire": expire.strftime("%Y-%m-%d"),
        "used": False
    }
    return token


def verify_cafe24_webhook(req) -> bool:
    if not CAFE24_WEBHOOK_SECRET:
        return True  # 시크릿 미설정 시 검증 생략
    signature = req.headers.get("X-Cafe24-Signature", "")
    body = req.get_data()
    expected = hmac.new(
        CAFE24_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_coolsms_auth_header() -> str:
    """쿨SMS HMAC 인증 헤더 생성"""
    date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = secrets.token_hex(16)
    data = date + salt
    signature = hmac.new(
        COOLSMS_API_SECRET.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()
    return f'HMAC-SHA256 apiKey={COOLSMS_API_KEY}, date={date}, salt={salt}, signature={signature}'


def send_alimtalk(receiver_phone: str, receiver_name: str,
                  sender_name: str, gift_message: str,
                  order_id: str, token: str, expire_date: str) -> bool:
    """쿨SMS HTTP API로 카카오 알림톡 발송"""
    if not COOLSMS_API_KEY or not COOLSMS_API_SECRET:
        logger.warning("쿨SMS API 키 미설정 - 알림톡 발송 건너뜀")
        return False

    address_url = (
        f"{GIFT_ADDRESS_URL}"
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
                "from": COOLSMS_SENDER.replace("-", ""),
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
                    "buttons": [
                        {
                            "buttonType": "WL",
                            "buttonName": "배송지 입력하기",
                            "linkMo": address_url,
                            "linkPc": address_url
                        }
                    ]
                }
            }
        }
        resp = requests.post(
            "https://api.coolsms.co.kr/messages/v4/send",
            headers={
                "Authorization": get_coolsms_auth_header(),
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10
        )
        if resp.status_code in (200, 201):
            logger.info(f"알림톡 발송 성공: {receiver_phone} / 주문번호: {order_id}")
            return True
        else:
            logger.error(f"알림톡 발송 실패: {resp.status_code} {resp.text[:300]}")
            return send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, address_url, expire_date)
    except Exception as e:
        logger.error(f"알림톡 발송 오류: {e}")
        return send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, address_url, expire_date)


def send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, address_url, expire_date) -> bool:
    """알림톡 실패 시 SMS 대체 발송"""
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
                "from": COOLSMS_SENDER.replace("-", ""),
                "type": "LMS",
                "text": text
            }
        }
        resp = requests.post(
            "https://api.coolsms.co.kr/messages/v4/send",
            headers={
                "Authorization": get_coolsms_auth_header(),
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10
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


# 리프레시 토큰 저장소 (Railway 환경변수 CAFE24_REFRESH_TOKEN 에서 로드)
_refresh_token_store = {"token": os.getenv("CAFE24_REFRESH_TOKEN", "")}

RAILWAY_TOKEN = os.getenv("RAILWAY_TOKEN", "")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID", "")
RAILWAY_ENVIRONMENT_ID = os.getenv("RAILWAY_ENVIRONMENT_ID", "")


def save_refresh_token_to_railway(refresh_token: str):
    """Railway 환경변수에 리프레시 토큰 영구 저장"""
    if not RAILWAY_TOKEN or not RAILWAY_SERVICE_ID or not RAILWAY_ENVIRONMENT_ID:
        logger.warning("Railway 토큰 미설정 - 환경변수 저장 건너뜀 (서버 재시작 시 재인증 필요)")
        return
    try:
        query = """
        mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
          variableCollectionUpsert(input: $input)
        }
        """
        variables = {
            "input": {
                "serviceId": RAILWAY_SERVICE_ID,
                "environmentId": RAILWAY_ENVIRONMENT_ID,
                "variables": {"CAFE24_REFRESH_TOKEN": refresh_token}
            }
        }
        resp = requests.post(
            "https://backboard.railway.app/graphql/v2",
            headers={
                "Authorization": f"Bearer {RAILWAY_TOKEN}",
                "Content-Type": "application/json"
            },
            json={"query": query, "variables": variables},
            timeout=10
        )
        if resp.status_code == 200 and "errors" not in resp.json():
            logger.info("Railway 환경변수에 리프레시 토큰 저장 완료")
        else:
            logger.error(f"Railway 환경변수 저장 실패: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Railway 환경변수 저장 오류: {e}")


def get_cafe24_access_token() -> str:
    """카페24 OAuth 액세스 토큰 획득 (Authorization Code 방식, 리프레시 토큰 사용)"""
    global _token_cache, _refresh_token_store
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    refresh_token = _refresh_token_store.get("token", "")
    if not refresh_token:
        logger.error("리프레시 토큰 없음 - /oauth/install 로 OAuth 인증 필요")
        return None

    try:
        credentials = base64.b64encode(
            f"{CAFE24_CLIENT_ID}:{CAFE24_CLIENT_SECRET}".encode()
        ).decode()
        resp = requests.post(
            f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            },
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            _token_cache["token"] = data.get("access_token")
            _token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
            new_refresh = data.get("refresh_token")
            if new_refresh:
                _refresh_token_store["token"] = new_refresh
                save_refresh_token_to_railway(new_refresh)
                logger.info("리프레시 토큰 갱신 완료")
            logger.info("카페24 액세스 토큰 갱신 성공")
            return _token_cache["token"]
        else:
            logger.error(f"토큰 갱신 실패: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"토큰 요청 오류: {e}")
        return None


def update_cafe24_order_address(order_id, name, phone, zipcode, address1, address2, memo):
    """카페24 REST API로 주문 배송지 업데이트"""
    try:
        access_token = get_cafe24_access_token()
        if not access_token:
            logger.error("카페24 액세스 토큰 획득 실패")
            return False

        url = f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/admin/orders/{order_id}/shippingaddress"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Cafe24-Api-Version": "2024-06-01"
        }
        payload = {
            "shop_no": 1,
            "shipping_address": {
                "name": name,
                "phone": phone.replace("-", ""),
                "cellphone": phone.replace("-", ""),
                "zipcode": zipcode,
                "address1": address1,
                "address2": address2,
                "shipping_message": memo
            }
        }
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

@app.route("/health", methods=["GET"])
def health():
    has_token = bool(_refresh_token_store.get("token"))
    return jsonify({"status": "ok", "time": datetime.now().isoformat(), "oauth_ready": has_token})


@app.route("/admin/save_token", methods=["GET"])
def admin_save_token():
    """현재 메모리의 리프레시 토큰을 Railway 환경변수에 강제 저장"""
    token = _refresh_token_store.get("token", "")
    if not token:
        return jsonify({"error": "리프레시 토큰 없음 - OAuth 인증 먼저 필요"}), 400
    # Railway CLI 방식으로 환경변수 저장
    import subprocess
    try:
        result = subprocess.run(
            ["railway", "variables", "set", f"CAFE24_REFRESH_TOKEN={token}"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "RAILWAY_NO_TELEMETRY": "1"}
        )
        if result.returncode == 0:
            logger.info("Railway CLI로 리프레시 토큰 저장 완료")
            return jsonify({"success": True, "message": "Railway 환경변수에 저장 완료"})
        else:
            logger.error(f"Railway CLI 저장 실패: {result.stderr}")
            return jsonify({"success": False, "token_preview": token[:20] + "...", "error": result.stderr[:200]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "token_preview": token[:20] + "..."})


@app.route("/oauth/install", methods=["GET"])
def oauth_install():
    """카페24 OAuth 인증 시작 - 이 URL을 브라우저에서 열면 카페24 로그인 페이지로 이동"""
    import urllib.parse
    scope = "mall.read_order,mall.write_order"
    state = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CAFE24_CLIENT_ID,
        "redirect_uri": f"{SERVER_URL}/oauth/callback",
        "scope": scope,
        "state": state
    })
    auth_url = f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/authorize?{params}"
    return f"""<html><head><meta charset='UTF-8'></head><body>
    <h2>카페24 OAuth 인증</h2>
    <p>아래 버튼을 클릭하여 카페24 쇼핑몰 관리자 계정으로 로그인하세요.</p>
    <a href="{auth_url}" style="display:inline-block;padding:14px 28px;background:#222;color:#fff;text-decoration:none;border-radius:8px;font-size:16px;margin-top:20px">카페24 관리자 로그인으로 인증하기</a>
    </body></html>"""


@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    """카페24 OAuth 콜백 - 인증 코드를 액세스 토큰으로 교환"""
    code = request.args.get("code", "")
    error = request.args.get("error", "")

    if error:
        return f"<h2>인증 실패: {error}</h2>", 400

    if not code:
        return "<h2>인증 코드가 없습니다.</h2>", 400

    try:
        credentials = base64.b64encode(
            f"{CAFE24_CLIENT_ID}:{CAFE24_CLIENT_SECRET}".encode()
        ).decode()
        resp = requests.post(
            f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{SERVER_URL}/oauth/callback"
            },
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            _token_cache["token"] = data.get("access_token")
            _token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
            refresh_token = data.get("refresh_token", "")
            if refresh_token:
                _refresh_token_store["token"] = refresh_token
                save_refresh_token_to_railway(refresh_token)
                logger.info(f"[REFRESH_TOKEN_VALUE] {refresh_token}")
            logger.info("OAuth 인증 완료 - 액세스 토큰 및 리프레시 토큰 저장")
            return """<html><head><meta charset='UTF-8'></head><body>
            <h2 style='color:green'>✅ OAuth 인증 완료!</h2>
            <p>카페24 주문 API 연동이 완료되었습니다.</p>
            <p>이제 선물하기 기능이 정상 작동합니다.</p>
            <p style='color:#888;font-size:13px;margin-top:20px'>이 창을 닫으셔도 됩니다.</p>
            </body></html>"""
        else:
            logger.error(f"토큰 교환 실패: {resp.status_code} {resp.text[:300]}")
            return f"<h2>토큰 교환 실패: {resp.status_code}</h2><pre>{resp.text[:300]}</pre>", 400
    except Exception as e:
        logger.error(f"OAuth 콜백 오류: {e}")
        return f"<h2>오류 발생: {e}</h2>", 500


@app.route("/", methods=["GET"])
def health_check():
    """헬스 체크 엔드포인트"""
    return jsonify({"status": "ok", "service": "gift-server"}), 200


@app.route("/api/status", methods=["GET"])
def api_status():
    """API 상태 확인 엔드포인트"""
    return jsonify({
        "status": "running",
        "service": "gift-server",
        "version": "1.0",
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route("/webhook/cafe24/order", methods=["POST"])
def cafe24_order_webhook():
    """카페24 주문 완료 웹훅 수신"""
    if not verify_cafe24_webhook(request):
        logger.warning("웹훅 서명 검증 실패")
        return jsonify({"error": "Invalid signature"}), 401

    try:
        data = request.get_json(force=True)
        logger.info(f"웹훅 수신: {json.dumps(data, ensure_ascii=False)[:300]}")
    except Exception as e:
        logger.error(f"웹훅 JSON 파싱 실패: {e}")
        return jsonify({"error": "Invalid JSON"}), 400

    event_name = data.get("event_name", "")
    if event_name not in ("mall.read_order", "order_paid", ""):
        return jsonify({"status": "ignored"}), 200

    resource = data.get("resource", {})
    order_id       = resource.get("order_id") or data.get("order_id", "")
    order_memo     = resource.get("order_memo", "") or ""
    is_gift        = resource.get("is_gift", "0")
    receiver_name  = resource.get("gift_receiver_name", "")
    receiver_phone = resource.get("gift_receiver_phone", "")
    gift_message   = resource.get("gift_message", "")
    sender_name    = resource.get("buyer_name", "") or resource.get("member_id", "고객")

    if str(is_gift) != "1" and "선물주문" not in order_memo:
        return jsonify({"status": "not_gift_order"}), 200

    if not receiver_phone:
        logger.warning(f"받는 분 전화번호 없음: {order_id}")
        return jsonify({"status": "no_receiver_phone"}), 200

    token = generate_token(order_id)
    expire_date = (datetime.now() + timedelta(days=GIFT_EXPIRE_DAYS)).strftime("%Y년 %m월 %d일")

    success = send_alimtalk(
        receiver_phone=receiver_phone,
        receiver_name=receiver_name or "고객",
        sender_name=sender_name,
        gift_message=gift_message,
        order_id=order_id,
        token=token,
        expire_date=expire_date
    )

    if success:
        return jsonify({"status": "success", "order_id": order_id}), 200
    else:
        return jsonify({"status": "send_failed", "order_id": order_id}), 500


@app.route("/gift/address", methods=["GET"])
def gift_address_page():
    """배송지 입력 페이지 (GET)"""
    return ADDRESS_PAGE_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route("/gift/address", methods=["POST"])
def save_gift_address():
    """배송지 저장 및 카페24 주문 업데이트"""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"success": False, "message": "잘못된 요청입니다."}), 400

    token    = data.get("token", "")
    order_id = data.get("order_id", "")

    token_info = gift_tokens.get(token)
    if not token_info:
        return jsonify({"success": False, "message": "유효하지 않은 링크입니다."}), 400
    if token_info.get("used"):
        return jsonify({"success": False, "message": "이미 배송지가 입력된 선물입니다."}), 400
    try:
        expire_dt = datetime.strptime(token_info.get("expire", ""), "%Y-%m-%d")
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

    success = update_cafe24_order_address(
        order_id=order_id,
        name=recv_name,
        phone=recv_phone,
        zipcode=recv_zip,
        address1=recv_addr1,
        address2=recv_addr2,
        memo=recv_memo
    )

    if success:
        gift_tokens[token]["used"] = True
        logger.info(f"배송지 등록 완료: 주문번호={order_id}")
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "배송지 등록 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}), 500


@app.route("/admin/gifts")
def admin_gifts():
    """선물 주문 현황 조회"""
    rows = []
    for token, info in gift_tokens.items():
        rows.append(f"<tr><td>{info['order_id']}</td><td>{info['expire']}</td><td>{'✅ 완료' if info['used'] else '⏳ 대기'}</td></tr>")
    html = f"""<html><head><meta charset='UTF-8'><title>선물 주문 관리</title>
    <style>body{{font-family:sans-serif;padding:20px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px}}</style></head>
    <body><h2>🎁 선물 주문 현황 (총 {len(gift_tokens)}건)</h2>
    <table><tr><th>주문번호</th><th>만료일</th><th>상태</th></tr>{''.join(rows)}</table></body></html>"""
    return html


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


# ──────────────────────────────────────────────
if __name__ == "__main__":
    import subprocess
    import sys
    
    port = os.getenv("PORT", "5000")
    logger.info(f"선물하기 서버 시작 (포트: {port})")
    
    # Render 환경에서는 gunicorn 사용, 로컬에서는 Flask 개발 서버 사용
    if os.getenv("RENDER"):
        # Render 환경: gunicorn 사용
        subprocess.run([
            sys.executable, "-m", "gunicorn",
            "-w", "2",
            "-b", f"0.0.0.0:{port}",
            "app:app"
        ])
    else:
        # 로컬 환경: Flask 개발 서버 사용
        app.run(host="0.0.0.0", port=int(port), debug=False)
