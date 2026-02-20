"""
선물하기 웹훅 서버
====================
카페24 주문 완료 웹훅을 수신하여
선물 받는 사람에게 카카오 알림톡으로 배송지 입력 링크를 자동 발송합니다.

실행 방법:
  pip install flask requests coolsms_python_sdk
  python app.py

배포 시:
  gunicorn -w 2 -b 0.0.0.0:5000 app:app
"""

import os
import json
import hmac
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
import requests

# ──────────────────────────────────────────────
# 설정값 (환경변수 또는 직접 입력)
# ──────────────────────────────────────────────
CAFE24_MALL_ID      = os.getenv("CAFE24_MALL_ID",      "pathocrionwater")
CAFE24_CLIENT_ID    = os.getenv("CAFE24_CLIENT_ID",    "NiqeMRujVKFU1zE5OWzUID")
CAFE24_CLIENT_SECRET= os.getenv("CAFE24_CLIENT_SECRET","Yb3GU2KIKZltbQZrgojV1C")
CAFE24_WEBHOOK_SECRET=os.getenv("CAFE24_WEBHOOK_SECRET","여기에_웹훅_시크릿키")

# 쿨SMS (알림톡 발송용) - https://coolsms.co.kr
COOLSMS_API_KEY     = os.getenv("COOLSMS_API_KEY",     "여기에_쿨SMS_API_KEY")
COOLSMS_API_SECRET  = os.getenv("COOLSMS_API_SECRET",  "여기에_쿨SMS_API_SECRET")
COOLSMS_SENDER      = os.getenv("COOLSMS_SENDER",      "여기에_발신번호_010XXXXXXXX")
KAKAO_CHANNEL_ID    = os.getenv("KAKAO_CHANNEL_ID",    "여기에_카카오_채널_ID_@채널명")
KAKAO_TEMPLATE_ID   = os.getenv("KAKAO_TEMPLATE_ID",   "여기에_알림톡_템플릿_코드")

# 배송지 입력 페이지 URL
GIFT_ADDRESS_URL    = os.getenv("GIFT_ADDRESS_URL",
    f"https://{CAFE24_MALL_ID}.cafe24.com/web/upload/gift_address.html")

# 선물 주문 만료일 (일 수)
GIFT_EXPIRE_DAYS    = int(os.getenv("GIFT_EXPIRE_DAYS", "7"))

# ──────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 간단한 인메모리 토큰 저장소 (운영 시 DB/Redis로 교체 권장)
gift_tokens = {}


# ──────────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────────
def generate_token(order_id: str) -> str:
    """고유 토큰 생성 및 저장"""
    token = secrets.token_urlsafe(32)
    expire = datetime.now() + timedelta(days=GIFT_EXPIRE_DAYS)
    gift_tokens[token] = {
        "order_id": order_id,
        "expire": expire.strftime("%Y-%m-%d"),
        "used": False
    }
    return token


def verify_cafe24_webhook(request) -> bool:
    """카페24 웹훅 서명 검증"""
    if not CAFE24_WEBHOOK_SECRET or CAFE24_WEBHOOK_SECRET.startswith("여기에"):
        return True  # 시크릿 미설정 시 검증 생략 (개발 환경)
    signature = request.headers.get("X-Cafe24-Signature", "")
    body = request.get_data()
    expected = hmac.new(
        CAFE24_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def send_alimtalk(receiver_phone: str, receiver_name: str,
                  sender_name: str, gift_message: str,
                  order_id: str, token: str, expire_date: str) -> bool:
    """쿨SMS를 통한 카카오 알림톡 발송"""
    try:
        from coolsms_python_sdk import DefaultApi, ApiClient, Configuration, SendMessageRequest

        address_url = (
            f"{GIFT_ADDRESS_URL}"
            f"?token={token}"
            f"&order_id={order_id}"
            f"&from={requests.utils.quote(sender_name)}"
            f"&msg={requests.utils.quote(gift_message)}"
            f"&shop={requests.utils.quote(CAFE24_MALL_ID)}"
            f"&expire={expire_date}"
        )

        # 알림톡 템플릿 변수 치환
        # 템플릿 내용 예시:
        # "[선물 도착] #{받는분이름}님, #{보내는분이름}님이 선물을 보내셨어요!
        #  아래 버튼을 눌러 배송지를 입력해 주세요.
        #  ※ #{만료일}까지 입력하지 않으면 자동 취소됩니다."
        template_args = {
            "#{받는분이름}": receiver_name,
            "#{보내는분이름}": sender_name,
            "#{만료일}": expire_date,
            "#{선물메시지}": gift_message if gift_message else "마음을 담아 보낸 선물입니다.",
            "#{배송지입력링크}": address_url
        }

        config = Configuration()
        config.api_key["Authorization"] = COOLSMS_API_KEY
        config.api_key_prefix["Authorization"] = COOLSMS_API_SECRET

        api = DefaultApi(ApiClient(config))
        msg = {
            "to": receiver_phone.replace("-", ""),
            "from": COOLSMS_SENDER.replace("-", ""),
            "type": "ATA",  # 알림톡
            "kakaoOptions": {
                "pfId": KAKAO_CHANNEL_ID,
                "templateId": KAKAO_TEMPLATE_ID,
                "variables": template_args,
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
        api.send_many({"messages": [msg]})
        logger.info(f"알림톡 발송 성공: {receiver_phone} / 주문번호: {order_id}")
        return True

    except Exception as e:
        logger.error(f"알림톡 발송 실패: {e}")
        # 알림톡 실패 시 SMS 대체 발송
        return send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, token, expire_date)


def send_sms_fallback(receiver_phone, receiver_name, sender_name, order_id, token, expire_date) -> bool:
    """알림톡 실패 시 SMS 대체 발송"""
    try:
        from coolsms_python_sdk import DefaultApi, ApiClient, Configuration

        address_url = (
            f"{GIFT_ADDRESS_URL}"
            f"?token={token}&order_id={order_id}"
            f"&from={requests.utils.quote(sender_name)}"
            f"&expire={expire_date}"
        )
        text = (
            f"[선물 도착] {receiver_name}님,\n"
            f"{sender_name}님이 선물을 보내셨어요!\n"
            f"아래 링크에서 배송지를 입력해 주세요.\n"
            f"{address_url}\n"
            f"※ {expire_date}까지 입력 필요"
        )
        config = Configuration()
        config.api_key["Authorization"] = COOLSMS_API_KEY
        config.api_key_prefix["Authorization"] = COOLSMS_API_SECRET

        api = DefaultApi(ApiClient(config))
        api.send_many({"messages": [{
            "to": receiver_phone.replace("-", ""),
            "from": COOLSMS_SENDER.replace("-", ""),
            "type": "LMS",
            "text": text
        }]})
        logger.info(f"SMS 대체 발송 성공: {receiver_phone}")
        return True
    except Exception as e:
        logger.error(f"SMS 대체 발송도 실패: {e}")
        return False


# ──────────────────────────────────────────────
# 라우트
# ──────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """서버 상태 확인"""
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


@app.route("/webhook/cafe24/order", methods=["POST"])
def cafe24_order_webhook():
    """
    카페24 주문 완료 웹훅 수신 엔드포인트
    카페24 개발자 어드민에서 이 URL을 웹훅 URL로 등록하세요.
    이벤트: mall.read_order (주문 완료)
    """
    # 서명 검증
    if not verify_cafe24_webhook(request):
        logger.warning("웹훅 서명 검증 실패")
        return jsonify({"error": "Invalid signature"}), 401

    try:
        data = request.get_json(force=True)
        logger.info(f"웹훅 수신: {json.dumps(data, ensure_ascii=False)[:300]}")
    except Exception as e:
        logger.error(f"웹훅 JSON 파싱 실패: {e}")
        return jsonify({"error": "Invalid JSON"}), 400

    # 이벤트 타입 확인
    event_name = data.get("event_name", "")
    if event_name not in ("mall.read_order", "order_paid", ""):
        logger.info(f"처리 대상 아닌 이벤트: {event_name}")
        return jsonify({"status": "ignored"}), 200

    # 주문 데이터 추출
    resource = data.get("resource", {})
    order_id = resource.get("order_id") or data.get("order_id", "")

    # 주문 메모(order_memo)에서 선물 정보 파싱
    # gift_button.html에서 hidden input으로 전달한 값들
    order_memo     = resource.get("order_memo", "") or ""
    is_gift        = resource.get("is_gift", "0")
    receiver_name  = resource.get("gift_receiver_name", "")
    receiver_phone = resource.get("gift_receiver_phone", "")
    gift_message   = resource.get("gift_message", "")
    sender_name    = resource.get("buyer_name", "") or resource.get("member_id", "고객")

    # 선물 주문 여부 확인
    if str(is_gift) != "1" and "선물주문" not in order_memo:
        logger.info(f"일반 주문 (선물 아님): {order_id}")
        return jsonify({"status": "not_gift_order"}), 200

    if not receiver_phone:
        logger.warning(f"받는 분 전화번호 없음: {order_id}")
        return jsonify({"status": "no_receiver_phone"}), 200

    # 토큰 생성
    token = generate_token(order_id)
    expire_date = (datetime.now() + timedelta(days=GIFT_EXPIRE_DAYS)).strftime("%Y년 %m월 %d일")

    # 알림톡 발송
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
        logger.info(f"선물 알림톡 발송 완료: 주문번호={order_id}, 수신자={receiver_phone}")
        return jsonify({"status": "success", "order_id": order_id}), 200
    else:
        logger.error(f"선물 알림톡 발송 실패: 주문번호={order_id}")
        return jsonify({"status": "send_failed", "order_id": order_id}), 500


@app.route("/gift/address", methods=["POST"])
def save_gift_address():
    """
    선물 받는 사람이 배송지를 입력하면 카페24 주문에 배송지를 업데이트합니다.
    gift_address.html 에서 fetch로 호출합니다.
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"success": False, "message": "잘못된 요청입니다."}), 400

    token    = data.get("token", "")
    order_id = data.get("order_id", "")

    # 토큰 검증
    token_info = gift_tokens.get(token)
    if not token_info:
        return jsonify({"success": False, "message": "유효하지 않은 링크입니다."}), 400
    if token_info.get("used"):
        return jsonify({"success": False, "message": "이미 배송지가 입력된 선물입니다."}), 400
    expire_str = token_info.get("expire", "")
    try:
        expire_dt = datetime.strptime(expire_str, "%Y-%m-%d")
        if datetime.now() > expire_dt:
            return jsonify({"success": False, "message": "배송지 입력 기한이 지났습니다."}), 400
    except Exception:
        pass

    recv_name  = data.get("recv_name", "")
    recv_phone = data.get("recv_phone", "")
    recv_zip   = data.get("recv_zip", "")
    recv_addr1 = data.get("recv_addr1", "")
    recv_addr2 = data.get("recv_addr2", "")
    recv_memo  = data.get("recv_memo", "")

    if not all([recv_name, recv_phone, recv_zip, recv_addr1]):
        return jsonify({"success": False, "message": "필수 항목을 모두 입력해 주세요."}), 400

    # 카페24 API로 주문 배송지 업데이트
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


def update_cafe24_order_address(order_id, name, phone, zipcode, address1, address2, memo):
    """카페24 REST API로 주문 배송지 업데이트"""
    try:
        # 액세스 토큰 획득 (실제 운영 시 OAuth 토큰 관리 필요)
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
            logger.error(f"카페24 배송지 업데이트 실패: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"카페24 API 호출 오류: {e}")
        return False


def get_cafe24_access_token():
    """카페24 OAuth 액세스 토큰 획득 (Client Credentials 방식)"""
    try:
        import base64
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
                "grant_type": "client_credentials",
                "scope": "mall.write_order"
            },
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
        else:
            logger.error(f"토큰 획득 실패: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"토큰 요청 오류: {e}")
        return None


# ──────────────────────────────────────────────
# 배송지 입력 페이지 (GET)
# ──────────────────────────────────────────────
ADDRESS_PAGE_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>선물 배송지 입력</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Apple SD Gothic Neo', sans-serif; background: #f8f8f8; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .wrap { background: #fff; border-radius: 16px; padding: 36px 28px; max-width: 420px; width: 100%; box-shadow: 0 4px 24px rgba(0,0,0,0.08); }
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
  .result h2 { margin-bottom: 12px; }
</style>
</head>
<body>
<div class="wrap" id="app"></div>
<script src="//t1.daumcdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js"></script>
<script>
const params = new URLSearchParams(location.search);
const token = params.get('token') || '';
const orderId = params.get('order_id') || '';
const sender = decodeURIComponent(params.get('from') || '');
const msg = decodeURIComponent(params.get('msg') || '');
const expire = params.get('expire') || '';
const app = document.getElementById('app');

app.innerHTML = `
  <div class="gift-icon">🎁</div>
  <h2>${sender ? sender + '님이 선물을 보냈어요!' : '선물이 도착했어요!'}</h2>
  <p class="sender">배송지를 입력하시면 선물을 받으실 수 있습니다</p>
  ${msg ? '<div class="msg-box">💌 ' + msg + '</div>' : ''}
  <label>받는 분 이름</label>
  <input type="text" id="name" placeholder="이름을 입력하세요" required>
  <label>연락처</label>
  <input type="tel" id="phone" placeholder="010-0000-0000" required>
  <label>우편번호</label>
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
      app.innerHTML = '<div class="result"><div style="font-size:60px">🎉</div><h2 style="color:#2ecc71">배송지가 등록되었습니다!</h2><p style="color:#888;font-size:14px">선물이 곧 배송될 예정입니다.<br>감사합니다!</p></div>';
    } else {
      alert(result.message || '오류가 발생했습니다.');
      btn.disabled = false;
      btn.textContent = '배송지 등록하기';
    }
  } catch(e) {
    alert('네트워크 오류가 발생했습니다.');
    btn.disabled = false;
    btn.textContent = '배송지 등록하기';
  }
}
</script>
</body>
</html>
"""

@app.route("/gift/address", methods=["GET"])
def gift_address_page():
    """배송지 입력 페이지 (GET)"""
    return ADDRESS_PAGE_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}


# 관리자 선물 주문 목록
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
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    logger.info(f"선물하기 서버 시작 (포트: {port})")
    app.run(host="0.0.0.0", port=port, debug=debug)
