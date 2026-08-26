# 투자 시장 대시보드

국내 투자자 수급, KOSPI·KOSDAQ 비차익 프로그램매매, 미국 유동성 지표를
Streamlit에서 확인하는 읽기 전용 대시보드입니다.

## 운영 구조

- Streamlit은 저장된 JSON 캐시만 읽으며 KIS 토큰을 발급하지 않습니다.
- GitHub Actions가 평일 15:47 KST에 실행되며, 예약 누락에 대비해 16:17 KST 보조 실행을 둡니다.
- 두 실행은 같은 잠금과 일일 가드를 사용하므로 KIS 토큰은 하루 최대 한 번만 요청합니다.
- 배치 한 번에서 KIS 토큰을 최대 한 번만 요청하고 모든 국내 데이터 호출이
  그 토큰을 공유합니다.
- `data/kis_batch_state.json`에 토큰 요청 날짜를 먼저 기록하므로 같은 날짜에
  작업을 재실행해도 두 번째 토큰 요청은 거부됩니다.
- 토큰이나 앱 시크릿은 파일·커밋·Streamlit 화면에 저장하지 않습니다.

## 화면

1. **국내 수급 스캐너**: KOSPI 200·KOSDAQ 150 외인/기관 5일 수급과 종목 차트
2. **비차익 프로그램매매**: KOSPI·KOSDAQ 일별 비차익 순매수와 기간 누적
3. **미국 유동성**: TGA, M2, Overnight Reverse Repo, 지급준비금

미국 지표는 FRED의 원 발표 주기를 유지하고 화면 단위만 십억 달러로 통일합니다.

## 설정

GitHub 저장소의 Actions secret에 다음 두 값이 필요합니다.

- `KIS_APP_KEY`
- `KIS_APP_SECRET`

Streamlit Secrets에는 KIS 키가 필요하지 않습니다. 앱은 캐시 읽기 전용입니다.

## 로컬 실행과 테스트

```bash
pip install -r requirements.txt
streamlit run app.py
python -m unittest discover -s tests -v
```

국내 캐시는 KIS 공식 API, 미국 유동성 캐시는 FRED CSV를 사용합니다. 캐시가
누락되거나 수집 결과의 기준일이 당일과 다르면 기존 정상 캐시를 보존합니다.
