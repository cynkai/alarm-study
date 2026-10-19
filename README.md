# 이 알람, 장애를 잡을 수 있을까? — 실습 환경

클라우드클럽 10기 시즌2 스터디의 공통 실습 환경입니다.
`docker compose up` 한 번이면 연습용 앱과 관측 도구가 내 노트북에 뜹니다.
모두가 **같은 집**에서, **각자 다른 경보기**를 달고, **같은 장애**를 맞습니다.

## 준비물

- Docker Desktop (맥은 Apple Silicon·Intel 모두, 윈도우는 WSL2 기반)
- Git
- 브라우저

맥(Apple Silicon)과 윈도우(Docker Desktop)에서 확인했습니다.

## 처음 띄우기

```bash
git clone https://github.com/cynkai/alarm-study.git
cd alarm-study
docker compose up -d --build
docker compose ps
```

서비스 6개가 모두 `Up`이면 성공입니다. 처음엔 이미지를 받느라 몇 분 걸려요.

| 주소 | 무엇 | 확인할 것 |
| --- | --- | --- |
| http://localhost:3000 | Grafana | Dashboards → 조립 키트 → 조립 키트 대시보드 |
| http://localhost:9090 | Prometheus | Status → Target health 에서 `app`, `prometheus`가 UP |
| http://localhost:9093 | Alertmanager | 화면이 뜨면 됨 |

모든 포트는 내 노트북(127.0.0.1)에서만 열립니다. 같은 와이파이의 다른 사람은 접속할 수 없어요.

## 구성

| 서비스 | 하는 일 |
| --- | --- |
| `app` | 연습용 앱. 엔드포인트 `/`, `/products`, `/checkout` |
| `payments` | 앱이 부르는 외부 결제·재고 서비스 (남의 서비스라 내부 지표는 없음) |
| `loadgen` | 가짜 사용자. 초당 약 5건 |
| `prometheus` | 지표 수집과 알람 룰 평가 |
| `alertmanager` | 울린 알람을 받음 |
| `grafana` | 대시보드 |
| `fault` | 세션 때 장애를 거는 도구 |
| `check` | 내 설계 점검과 결과 계산 도구 |

## 내 설계를 넣는 곳

- **알람 룰**: `prometheus/rules/` 안에 `.yml` 파일로. 예시는 `kit.yml`
- **대시보드**: Grafana 화면에서 만들고, 대시보드 → Export → JSON 으로 저장해서 제출

조립 키트(예시 알람 1개 + 예시 대시보드 1개)는 **출발점**입니다. 빈칸만 바꿔도 내 설계가 되고, 잘 아는 분은 무시해도 됩니다.

## 매주 쓰는 명령

**수요일 마감 전: 내 설계 점검**

```bash
docker compose run --rm check
```

문법 / 로드 / 지표 존재 세 가지를 확인합니다. "모두 통과"가 나와야 금요일에 내 알람이 제대로 평가됩니다.

**금요일 세션: 장애 걸기**

리더가 채팅으로 `docker compose run --rm fault start ...` 한 줄을 보냅니다. **그 줄을 통째로** 복사해서 실행하세요.

```bash
docker compose run --rm fault status
```

`대기 중` 또는 `진행 중`이 나오면 정상입니다.

**세션 후: 내 결과 보기**

```bash
docker compose run --rm check result
```

알람이 장애 시작 후 몇 분 만에 울렸는지, 몇 번 울렸는지, Pending만 하다 끝났는지 계산해 줍니다.

## 세션 규칙

- **19:50까지** 실습 환경을 켜두세요. 장애 전 기준선이 필요합니다
- **세션 중에는 `--build`, `restart`, `down` 금지.** 앱이 다시 뜨는 몇 초 동안 에러가 나서, 장애 전에 가짜 알람이 생깁니다
- 번들 업데이트는 수요일 전에 공지합니다. 그때 미리 `git pull` 후 `docker compose up -d --build` 하세요

## 자주 막히는 곳

- **윈도우 PowerShell에서 `curl`**: 다른 명령으로 바뀝니다. `curl.exe`로 쓰세요
- **명령에 `<코드>` 같은 꺾쇠가 있으면** 그대로 치지 마세요. 리더가 보내는 명령은 늘 완성된 한 줄입니다
- **실행 후 `What's next: Debug this Compose error with Gordon`이 보여도** 고장이 아닙니다. 점검 실패 등으로 끝났을 때 Docker Desktop이 붙이는 안내 문구예요. 그 위의 출력을 보세요
- **그래프는 색이 아니라 범례 글자로 읽으세요.** 같은 Pending이라도 함께 보이는 선이 몇 개냐에 따라 색이 바뀝니다
- **Grafana에서 고친 대시보드는 Export로 꼭 저장하세요.** 컨테이너가 새로 만들어지면 화면에서 고친 내용은 사라질 수 있습니다
- **포트가 이미 쓰이고 있다**는 에러: 3000, 8000, 9090, 9093을 다른 프로그램이 쓰고 있는지 확인하세요
- **노트북에서 안 돌아가면** 리더에게 말해 주세요. 실습 VM을 드립니다

## 정리하기

```bash
docker compose down        # 멈춤. 지표 기록은 남음
docker compose down -v     # 멈추고 기록까지 삭제 (스터디 중에는 쓰지 마세요)
```

---

Grafana, Prometheus, Alertmanager, Docker, GitHub, Discord는 각 소유자의 상표이며, 이 스터디는 이들과 제휴 관계가 아닙니다.
