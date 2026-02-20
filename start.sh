#!/bin/bash
# 선물하기 서버 실행 스크립트

# 환경변수 로드
if [ -f .env ]; then
  export $(cat .env | grep -v '#' | xargs)
fi

# 패키지 설치
pip install -r requirements.txt -q

# 서버 실행 (운영 환경)
echo "선물하기 서버 시작..."
gunicorn -w 2 -b 0.0.0.0:${PORT:-5000} app:app --access-logfile - --error-logfile -
