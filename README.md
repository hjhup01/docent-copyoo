# 나의 전시 기록

MyPJT는 miniPJT와 별도로 실행되는 로컬 웹 앱입니다. Google 로그인 후 사용자 본인의 Drive에 생성되는 **나의 전시 기록** 스프레드시트에 Docent 수행 및 관람 기록을 저장합니다.

## 실행

1. `.env.example`의 내용을 `.env`에 설정합니다. miniPJT와 같은 Google OAuth 클라이언트를 사용할 수 있습니다.
2. Google Cloud OAuth 클라이언트의 승인된 리디렉션 URI에 `http://localhost:8502`를 등록합니다. miniPJT의 기본 포트(8501)는 그대로 사용할 수 있습니다.
3. Windows에서는 `run.bat`을 실행하거나 터미널에서 다음을 실행합니다.

   ```powershell
   py server.py
   ```

4. 브라우저에서 `http://localhost:8502`를 엽니다. (`127.0.0.1`이 아닌 `localhost` 주소를 사용해야 로그인 쿠키가 유지됩니다.)

서버는 이 PC의 `127.0.0.1`에서만 수신합니다. `.env`의 OAuth 비밀값과 로그인 토큰은 앱 코드나 Google 시트에 저장하지 않습니다. 앱은 로그인 세션을 메모리에 유지하므로 서버를 다시 시작하면 다시 로그인해야 합니다.

## 기록 시트

사용자가 처음 로그인하면 개인 Google Drive에 `나의 전시 기록` 스프레드시트를 만들고 `Docent 수행기록`, `관람 기록` 탭을 생성합니다. 저장 또는 수정할 때 각 탭의 기록을 갱신합니다.

## 테스트

```powershell
py -m pytest -q
```

## 데이터 저장 방식

저장할 때마다 전체 기록을 한 번의 Sheets `values:batchUpdate` 요청으로 덮어씁니다. 이전보다 줄어든 행은 같은 요청 안에서 빈 값으로 채우므로, 요청이 중간에 실패해도 기존 기록이 지워지지 않습니다. 기록을 불러오지 못한 상태에서는 빈 목록으로 시트를 덮어쓰지 않도록 추가·수정·삭제가 비활성화됩니다.

## 공공데이터 전시 검색 (선택)

전시 기록을 추가할 때 한국문화정보원 「한눈에보는문화정보 조회서비스」에서 전시를 검색해 제목·공간·일정·상세 정보를 채울 수 있습니다. 검색 결과가 없거나 키가 없으면 **직접 입력**으로 기록합니다.

1. [공공데이터포털 서비스 페이지](https://www.data.go.kr/data/15138937/openapi.do)에서 **활용신청**을 합니다(자동 승인).
2. 마이페이지 → 개발계정에서 발급된 **일반 인증키**(Encoding 또는 Decoding 아무거나)를 복사합니다.
3. `.env`에 `CULTURE_API_KEY=발급받은키`를 추가하고 서버를 재시작합니다. 승인 직후에는 키가 반영되기까지 1시간 정도 걸릴 수 있습니다.

서비스 키는 서버에서만 사용하며 브라우저로 전달되지 않습니다.

## 디자인

miniPJT와 같은 **다크 갤러리** 테마(어두운 전시실 + 금색 포인트)를 씁니다. 색 값은 `styles.css`의 `:root` 변수에 모여 있으며 miniPJT의 `.streamlit/config.toml` `[theme]`과 `src/app.py`의 색 상수와 같은 값입니다. 두 앱을 합칠 때는 이 변수만 맞추면 됩니다.

- 레이아웃: 왼쪽 사이드바(로고·통계 카드·계정 카드) + 위쪽 탭 + 목록 칸/상세 칸
- 상태 배지 색: 전시중(초록) · 전시예정(파랑) · 지난전시(회색) — miniPJT `BADGE_COLORS`와 동일

## Streamlit 버전 (Streamlit Cloud 배포용)

웹 버전(`server.py`)과 같은 시트·공공데이터 코드를 쓰는 Streamlit 화면이 `streamlit_app.py`에 있습니다. 로그인은 miniPJT와 같은 Streamlit 내장 구글 로그인(`st.login`)을 씁니다.

### 이 PC에서 실행

1. `.streamlit/secrets.toml.example`을 `.streamlit/secrets.toml`로 복사해 값을 채웁니다(`redirect_uri`는 `http://localhost:8503/oauth2callback`).
2. 구글 클라우드 콘솔 → OAuth 클라이언트 → **승인된 리디렉션 URI**에 `http://localhost:8503/oauth2callback`을 추가합니다.
3. `run_streamlit.bat`을 실행하고 `http://localhost:8503`을 엽니다.

### Streamlit Cloud 배포

1. 이 폴더를 GitHub 저장소에 올립니다(`.env`, `.streamlit/secrets.toml`은 `.gitignore`로 제외됨).
2. https://share.streamlit.io → **Create app** → 저장소 선택, **Main file path = `streamlit_app.py`**.
3. **Advanced settings → Secrets**에 `secrets.toml` 내용을 붙여 넣고 `redirect_uri`를 `https://<앱주소>.streamlit.app/oauth2callback`으로 바꿉니다.
4. 구글 클라우드 콘솔의 승인된 리디렉션 URI에 같은 주소를 추가합니다.
5. OAuth 동의 화면이 "테스트" 상태라면 사용할 구글 계정을 **테스트 사용자**에 추가합니다.

### 테스트

```powershell
py -m pytest -q
```
