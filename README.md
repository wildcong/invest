# 투자 시장 대시보드

국내 투자자 수급, KOSPI·KOSDAQ 비차익 프로그램매매, 미국 유동성 지표를
Streamlit에서 확인하는 읽기 전용 대시보드입니다.

## 운영 구조

- Streamlit은 저장된 JSON 캐시만 읽으며 KIS 토큰을 발급하지 않습니다.
- GitHub Actions가 평일 15:47 KST에 실행되며, 전날 토큰의 실제 만료시각이
  늦어진 경우를 위해 16:47·17:47 KST 보조 실행을 둡니다.
- 모든 실행은 같은 동시성 잠금을 사용합니다. KIS가 응답한 실제 만료시각까지
  기존 토큰을 재사용하므로 여러 예약이 겹쳐도 새 토큰은 한 번만 발급됩니다.
- 토큰은 기존 `KIS_APP_SECRET`에서 파생한 키로 AES-256-GCM 암호화한 뒤
  `data/kis_batch_state.json`에 저장합니다. 앱 시크릿과 평문 토큰은 파일,
  커밋, Streamlit 화면에 저장하지 않습니다.
- 배치는 미국 유동성·비차익 프로그램매매를 먼저 수집하고 상태와 암호화 토큰을
  즉시 커밋한 뒤 수급 스캐너를 별도 단계로 실행합니다. 스캐너가 실패하거나
  제한시간을 넘겨도 앞 단계의 데이터는 보존됩니다.
- 각 API 단계는 같은 토큰으로 최대 3회 재시도하며 60초, 120초 순서의 지수
  백오프를 적용합니다.
- KIS 토큰 서버에 연결 자체가 성립하지 않은 `ConnectTimeout`만 5초, 10초 후
  안전하게 재시도합니다. 서버에 요청이 전달됐을 가능성이 있는 다른 오류는
  중복 발급 방지를 위해 자동 재발급하지 않습니다.
- 배치 상태에는 목표 거래일, 단계별 성공·실패·대기 상태, 시도 횟수, 실패 사유,
  마지막 갱신시각을 기록합니다. 실패한 토큰 요청은 30분 뒤 다시 시도할 수 있어
  만료 전 요청 실패가 하루 전체를 막지 않습니다.
- `workflow_dispatch` 수동 실행은 장 마감 전이나 주말에도 직전 완료 거래일을
  대상으로 동작하므로 누락된 배치를 즉시 복구할 수 있습니다.

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
페이지의 `수동 갱신` 버튼으로 GitHub Actions를 직접 실행하려면 다음 값을
Streamlit Secrets에 추가합니다.

- `GITHUB_ACTIONS_TOKEN`: `wildcong/invest` 저장소에만 접근할 수 있고
  **Actions: write** 권한만 부여한 fine-grained personal access token

토큰이 없으면 버튼은 GitHub Actions 수동 실행 화면을 안내합니다. 같은 거래일의
배치가 이미 성공했거나 방금 실행을 요청한 세션에서는 버튼을 비활성화하며,
GitHub 워크플로도 동시성 잠금과 캐시 기준일 검증으로 중복 수집을 건너뜁니다.

## 로컬 실행과 테스트

```bash
pip install -r requirements.txt
streamlit run app.py
python -m unittest discover -s tests -v
```

국내 캐시는 KIS 공식 API, 미국 유동성 캐시는 FRED CSV를 사용합니다. 캐시가
누락되거나 수집 결과의 기준일이 당일과 다르면 기존 정상 캐시를 보존합니다.
