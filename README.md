# Мониторинг: Grafana + Bitrix24 + MAX-мессенджер

**Репозиторий:** [github.com/matos85/grafana-bitrix-max-monitoring](https://github.com/matos85/grafana-bitrix-max-monitoring)

Проект поднимает на одном сервере **систему мониторинга**: графики, таблицы и вход через корпоративный портал **Bitrix24**. Отдельно принимает **события из MAX-мессенджера** (кто и какие действия выполнил) и показывает их на дашборде.

Всё запускается в **Docker** — не нужно вручную ставить Grafana, Prometheus и базу данных.

**Настройка:** все адреса, пароли и ключи — **только в файле `.env`** (создаётся из `.env.example`). В `docker-compose.yml`, `oauth-bridge/` и `max-metrics/` **нет зашитых IP и доменов**; без заполненного `.env` контейнеры не поднимутся. Внутри Docker сервисы общаются по именам (`grafana`, `prometheus`, `oauth-bridge`) — это не зависит от IP сервера.

---

## Простыми словами: что это даёт

| Что | Зачем |
|-----|--------|
| **Grafana** | Веб-страница с графиками и таблицами (как «панель управления»). Язык интерфейса — русский. |
| **Вход через Bitrix24** | Сотрудник нажимает «Войти через Bitrix24» — не нужно заводить отдельный пароль в Grafana (при первом входе создаётся учётка с правом просмотра). |
| **Локальный вход** | Можно войти логином и паролем администратора или «только просмотр» — задаётся в настройках на сервере. |
| **Prometheus** | Собирает числовые метрики (служебная часть; смотреть удобнее через Grafana). |
| **Приём событий MAX** | Внешняя система (MAX) отправляет JSON «пользователь X сделал действие Y» — счётчики растут, на дашборде видна активность. |

**Схема (упрощённо):**

```
Сотрудник → браузер → Grafana (графики)
                ↓
         Вход через Bitrix24 → oauth-bridge → Bitrix24

MAX-мессенджер → POST событий → max-metrics → Prometheus → Grafana (дашборд MAX)
```

---

## Что нужно перед установкой

1. **Сервер или ПК** с Linux (или другой ОС, где работает Docker).
2. **Docker** и **Docker Compose** (плагин `compose` в Docker).
3. **Свободные порты** на машине — задаются в `.env` (в `.env.example` для примера стенда):
   - `GRAFANA_PORT` (3002) — Grafana  
   - `OAUTH_BRIDGE_PORT` (4181) — OAuth-мост (Bitrix)  
   - `PROMETHEUS_PUBLIC_PORT` (9092) — Prometheus (через oauth2-proxy)  
   - `MAX_METRICS_PORT` (9093) — приём событий MAX  
4. **Приложение в Bitrix24** (локальное), если нужен вход через Bitrix — его создаёт администратор Bitrix24.
5. **IP или домен сервера**, как его открывают в браузере — прописывается в `.env` (`HOST_IP`, `GRAFANA_ROOT_URL` и др.; см. `.env.example`).

---

## Установка по шагам

### Шаг 1. Получить код проекта

```bash
git clone https://github.com/matos85/grafana-bitrix-max-monitoring.git
cd grafana-bitrix-max-monitoring
```

**Или** распаковать архив с проектом и перейти в папку с файлом `docker-compose.yml`.

### Шаг 2. Создать файл с настройками (секреты)

В репозитории **нет** настоящих паролей — только шаблон.

```bash
cp .env.example .env
```

Откройте **`.env`** и настройте под свою среду (полный список — в [`.env.example`](.env.example) и в разделе [Секреты](#секреты-и-чувствительные-данные) ниже).

**На новом сервере / другом ПК** обязательно замените:

- адреса: `HOST_IP`, `GRAFANA_ROOT_URL`, `GRAFANA_DOMAIN`, `OAUTH_BRIDGE_PUBLIC_URL`, `PROMETHEUS_PUBLIC_URL` (и порты в URL, если меняли `*_PORT`);
- Bitrix: `BITRIX_AUTH_BASE_URL`, `BITRIX_REST_BASE`, `BITRIX_TOKEN_URL`, `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `OAUTH_BRIDGE_CLIENT_ID`, `OAUTH_BRIDGE_CLIENT_SECRET`;
- пароли: `GF_ADMIN_PASSWORD`, `GF_VIEWER_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`;
- `OAUTH2_PROXY_COOKIE_SECRET` — **ровно 32 символа** (латиница/цифры).

**На том же стенде**, что в `.env.example`, можно оставить адреса из шаблона — но пароли и ключи Bitrix всё равно задайте свои (не из примера).

> `HOST_IP` — для удобства в документации и при ручной сборке URL; Docker читает **полные URL** (`GRAFANA_ROOT_URL`, `OAUTH_BRIDGE_PUBLIC_URL` и т.д.). Значения `HOST_IP` и URL должны **совпадать** по хосту.

### Адреса: ваш сервер и Bitrix24 (важно)

Есть **два разных набора** адресов — их не путают.

#### 1. Адреса **вашего** сервера с мониторингом

Машина с Docker. Пользователи и MAX обращаются по этим URL **из `.env`** (в репозитории пример — файл [`.env.example`](.env.example)):

| Переменная в `.env` | Что это | Используется в |
|---------------------|---------|----------------|
| `HOST_IP` | IP или домен (справочно, для сборки URL) | документация, ручная правка `.env` |
| `GRAFANA_ROOT_URL` | Полный URL Grafana | Grafana, выход из OAuth |
| `GRAFANA_DOMAIN` | Хост без `http://` | Grafana |
| `OAUTH_BRIDGE_PUBLIC_URL` | Публичный URL OAuth-моста | Bitrix Redirect URI, Grafana OAuth |
| `PROMETHEUS_PUBLIC_URL` | Prometheus через oauth2-proxy | oauth2-proxy |
| `GRAFANA_PORT` / `OAUTH_BRIDGE_PORT` / `PROMETHEUS_PUBLIC_PORT` / `MAX_METRICS_PORT` | Порты на хосте | `docker-compose` (проброс портов) |

Пример значений **текущего стенда** (не зашиты в код — только в `.env.example`): см. файл [`.env.example`](.env.example).

Порты можно сменить в `.env`; тогда обновите **те же** порты в `GRAFANA_ROOT_URL`, `OAUTH_BRIDGE_PUBLIC_URL`, `PROMETHEUS_PUBLIC_URL` и в URL для MAX (`http://<хост>:<MAX_METRICS_PORT>/...`).

**В кабинете Bitrix24** (настройки приложения) указывают **один** адрес с **вашего** сервера — Redirect URI:

```text
<OAUTH_BRIDGE_PUBLIC_URL>/oauth/callback
```

Пример: если `OAUTH_BRIDGE_PUBLIC_URL=http://monitoring.company.ru:4181`, то в Bitrix:

```text
http://monitoring.company.ru:4181/oauth/callback
```

Схема (`http` / `https`), хост, порт и путь `/oauth/callback` должны **совпадать буквально** с `.env`.

Grafana ходит к мосту по внутренней сети Docker (`http://oauth-bridge:8080/...`), но Bitrix и браузер пользователя — только по **`OAUTH_BRIDGE_PUBLIC_URL`**.

#### 2. Адреса **портала Bitrix24** (у каждой организации свои)

Это URL **вашего** портала Bitrix (облако или свой сервер). Берут из адресной строки портала или из карточки локального приложения.

| Переменная в `.env` | Назначение | Как получить |
|---------------------|------------|--------------|
| `BITRIX_AUTH_BASE_URL` | Страница входа OAuth (куда мост отправляет пользователя) | Обычно `https://ВАШ_ПОРТАЛ/oauth/authorize/` |
| `BITRIX_TOKEN_URL` | Обмен кода на токен | Часто **одинаковый для всех**: `https://oauth.bitrix.info/oauth/token/` |
| `BITRIX_REST_BASE` | REST API портала (ФИО, email пользователя) | Обычно `https://ВАШ_ПОРТАЛ/rest/` |

**Примеры портала:**

| Ваш Bitrix | `BITRIX_AUTH_BASE_URL` | `BITRIX_REST_BASE` |
|------------|------------------------|---------------------|
| `https://company.bitrix24.ru` | `https://company.bitrix24.ru/oauth/authorize/` | `https://company.bitrix24.ru/rest/` |
| Портал организации (см. `.env.example`) | `https://ВАШ_ПОРТАЛ/oauth/authorize/` | `https://ВАШ_ПОРТАЛ/rest/` |
| Коробка на своём домене | `https://portal.example.com/oauth/authorize/` | `https://portal.example.com/rest/` |

`BITRIX_TOKEN_URL` для облачного Bitrix24 почти всегда оставляют:

```text
https://oauth.bitrix.info/oauth/token/
```

Меняют только если в документации **вашей** установки указан другой token endpoint.

Дополнительно в `.env` (не URL, но связано с Bitrix):

| Переменная | Назначение |
|------------|------------|
| `BITRIX_CLIENT_ID` / `BITRIX_CLIENT_SECRET` | Ключи приложения из Bitrix24 |
| `OAUTH_BRIDGE_CLIENT_ID` / `OAUTH_BRIDGE_CLIENT_SECRET` | Те же ключи (Grafana и oauth2-proxy используют их) |
| `BITRIX_OAUTH_SCOPE` | Запрашиваемые права (`user,user_brief,profile,auth`) |
| `OAUTH_EMAIL_DOMAIN` | Домен для email-заглушки, если Bitrix не отдал почту (например `local`) |

Цепочка при входе:

```text
Grafana → OAUTH_BRIDGE_PUBLIC_URL/oauth/authorize
       → BITRIX_AUTH_BASE_URL (портал Bitrix, логин)
       → OAUTH_BRIDGE_PUBLIC_URL/oauth/callback
       → GRAFANA_ROOT_URL/login/generic_oauth
```

После смены любого адреса в `.env`:

```bash
docker compose --env-file .env up -d --build
```

И при смене `OAUTH_BRIDGE_PUBLIC_URL` — обновите Redirect URI в приложении Bitrix24.

### Шаг 3. Настроить Bitrix24 (если нужен вход через Bitrix)

В кабинете разработчика Bitrix24 для вашего приложения:

1. **Redirect URI** — см. таблицу выше: `{OAUTH_BRIDGE_PUBLIC_URL}/oauth/callback`

2. В `.env` прописать адреса **вашего портала** (`BITRIX_AUTH_BASE_URL`, `BITRIX_REST_BASE`, при необходимости `BITRIX_TOKEN_URL`).

3. Скопировать **Client ID** и **Client Secret** в `.env`:
   - `OAUTH_BRIDGE_CLIENT_ID` / `OAUTH_BRIDGE_CLIENT_SECRET`
   - `BITRIX_CLIENT_ID` / `BITRIX_CLIENT_SECRET` (обычно те же значения)

4. В правах приложения включить доступ к **пользователям** (`user` или `user_brief`), scope в `.env`:  
   `BITRIX_OAUTH_SCOPE=user,user_brief,profile,auth`

5. После смены прав — **переустановить** приложение на портале или заново войти через Bitrix.

### Шаг 4. Запустить

```bash
docker compose --env-file .env up -d --build
```

Первый запуск может занять несколько минут (скачивание образов, сборка моста и max-metrics).

Проверка, что контейнеры работают:

```bash
docker compose --env-file .env ps
```

Все сервисы в статусе `running` (или `healthy` для mysql) — нормально.

### Шаг 5. Открыть в браузере

Адреса берите **из вашего `.env`** (не из README):

| Страница | Переменная / путь в `.env` |
|----------|---------------------------|
| Grafana | `GRAFANA_ROOT_URL` |
| OAuth-мост (для Bitrix) | `OAUTH_BRIDGE_PUBLIC_URL` |
| Prometheus (с OAuth) | `PROMETHEUS_PUBLIC_URL` |
| Приём событий MAX | `http://<GRAFANA_DOMAIN>:<MAX_METRICS_PORT>/api/v1/events` |

Подставьте значения из `.env` (например `http://192.168.1.10:9093/api/v1/events`).

**Учётные записи Grafana** (из `.env`):

| Роль | Переменные | Назначение |
|------|------------|------------|
| Администратор | `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD` | Полные права |
| Только просмотр | `GF_VIEWER_USER` / `GF_VIEWER_PASSWORD` | Локальный пользователь без прав админа |

Первый вход через **Bitrix24** в Grafana создаёт пользователя с ролью **Viewer** (просмотр).

---

## Prometheus: эндпоинты и проверка после сборки

**Prometheus** собирает метрики со всех сервисов стека. Веб-интерфейс Prometheus доступен **с OAuth** (через Bitrix, как Grafana). Для графиков на русском удобнее **Grafana** — источник данных Prometheus уже подключён.

### Публичные URL (из `.env`)

Подставьте значения из вашего `.env` (пример портов — в `.env.example`):

| Сервис | Переменная / путь | Назначение |
|--------|-------------------|------------|
| Prometheus UI | `PROMETHEUS_PUBLIC_URL` | Targets, Graph, Alerts (вход через oauth2-proxy + Bitrix) |
| Grafana | `GRAFANA_ROOT_URL` | Дашборды, Explore → Prometheus |
| OAuth-мост | `OAUTH_BRIDGE_PUBLIC_URL` | OIDC для Grafana и oauth2-proxy |
| MAX ingest | `http://<GRAFANA_DOMAIN>:<MAX_METRICS_PORT>/api/v1/events` | POST событий (не Prometheus) |
| MAX health | `http://<GRAFANA_DOMAIN>:<MAX_METRICS_PORT>/health` | Проверка сервиса max-metrics |
| MAX metrics | `http://<GRAFANA_DOMAIN>:<MAX_METRICS_PORT>/metrics` | Сырые метрики (для отладки; основной сбор — через Prometheus) |

### Внутри Docker (между контейнерами)

| Target | URL | Кто опрашивает |
|--------|-----|----------------|
| Prometheus | `http://prometheus:9090` | сам себя, Grafana (datasource) |
| Grafana | `http://grafana:3000/metrics` | Prometheus (job `grafana`) |
| MAX metrics | `http://max-metrics:8080/metrics` | Prometheus (job `max-messenger`) |
| OAuth-мост | `http://oauth-bridge:8080/health` | ручная проверка / мониторинг |

Конфиг scrape: [`prometheus/prometheus.yml`](prometheus/prometheus.yml).

### Jobs Prometheus (что должно быть UP)

После `docker compose --env-file .env up -d --build` откройте **`PROMETHEUS_PUBLIC_URL`** → **Status → Targets** (или **Статус → Цели**):

| Job | Target | Метрики |
|-----|--------|---------|
| `prometheus` | `localhost:9090` | Служебные метрики TSDB |
| `grafana` | `grafana:3000` | Внутренние метрики Grafana |
| `max-messenger` | `max-metrics:8080` | `max_messenger_events_total{user_id, action}` |

Все три цели в состоянии **UP** — норма.

### Быстрая проверка с сервера

```bash
# Статус контейнеров
docker compose --env-file .env ps

# Health сервисов (подставьте хост/порты из .env)
curl -s "http://127.0.0.1:${MAX_METRICS_PORT:-9093}/health"
curl -s "http://127.0.0.1:${OAUTH_BRIDGE_PORT:-4181}/health"

# Тестовые события MAX → рост метрик
python3 scripts/generate-max-events.py --url "http://127.0.0.1:${MAX_METRICS_PORT:-9093}/api/v1/events"

# Метрика в Prometheus (из контейнера prometheus)
docker compose --env-file .env exec prometheus wget -qO- \
  'http://localhost:9090/api/v1/query?query=max_messenger_events_total' | head -c 500
```

В **Grafana** → **Explore** → Prometheus выполните запрос:

```promql
max_messenger_events_total
```

или откройте дашборд **«MAX — мониторинг мессенджера»**.

---

## Как пользоваться после установки

### Войти в Grafana

1. Откройте URL из `GRAFANA_ROOT_URL` в `.env`
2. Вариант А: логин и пароль из `GF_ADMIN_*` или `GF_VIEWER_*`
3. Вариант Б: кнопка **Bitrix24** → авторизация на портале Bitrix → возврат в Grafana

На главной странице — список дашбордов. Дашборд **«MAX — мониторинг мессенджера»** показывает активность по пользователям MAX.

### Отправить событие из MAX (для интеграции)

Сервис принимает **POST** с JSON:

```bash
# Подставьте хост и порт из .env: GRAFANA_DOMAIN и MAX_METRICS_PORT
curl -s -X POST "http://${GRAFANA_DOMAIN}:${MAX_METRICS_PORT}/api/v1/events" \
  -H "Content-Type: application/json" \
  -d '{
    "USER_ID": 797,
    "actions": {
      "send_message": 1,
      "open_chat": 2
    }
  }'
```

(В shell можно: `set -a && source .env && set +a` перед `curl`.)

| Поле | Описание |
|------|----------|
| `USER_ID` (или `user_id`) | Идентификатор пользователя в MAX |
| `actions` | Объект: название действия → на сколько увеличить счётчик (обычно `1`) |

Примеры действий: `send_message`, `open_chat`, `pay_device`, `read_message` — любые строки.

Если в `.env` задан `MAX_METRICS_API_KEY`, добавьте заголовок:

```bash
-H "X-API-Key: ваш_ключ"
```

или `Authorization: Bearer ваш_ключ`.

Успешный ответ: `{"status":"ok", ...}`.

### Остановить и снова запустить

```bash
# Остановить (данные в томах Docker сохраняются)
docker compose --env-file .env down

# Запустить снова
docker compose --env-file .env up -d
```

После изменения кода или конфигов:

```bash
docker compose --env-file .env up -d --build
```

---

## Репозиторий на GitHub

Проект: **[matos85/grafana-bitrix-max-monitoring](https://github.com/matos85/grafana-bitrix-max-monitoring)**

### Что можно выкладывать в репозиторий

| Файл / папка | В GitHub? |
|--------------|-----------|
| `docker-compose.yml`, код `oauth-bridge/`, `max-metrics/` | Да |
| `grafana/`, `prometheus/`, `scripts/` | Да |
| `.env.example` | Да (только **шаблон**, без реальных паролей и IP) |
| `README.md` | Да |
| **`.env`** | **Нет — никогда** |
| `token.txt`, ключи `*.pem` | **Нет** |

В проекте уже есть `.gitignore` — файл `.env` не попадёт в коммит, если не отключать игнор вручную.

### Развернуть на сервере из GitHub

```bash
git clone https://github.com/matos85/grafana-bitrix-max-monitoring.git
cd grafana-bitrix-max-monitoring
cp .env.example .env
nano .env   # заменить YOUR_SERVER_IP, Bitrix, пароли, Client ID/Secret
docker compose --env-file .env up -d --build
```

В `.env` на сервере **обязательно** свои `GRAFANA_ROOT_URL`, `OAUTH_BRIDGE_PUBLIC_URL`, `PROMETHEUS_PUBLIC_URL`, `GRAFANA_DOMAIN`, `BITRIX_*` и Redirect URI в Bitrix24.

Секреты на сервере живут **только в `.env` на диске сервера**, не в GitHub.

### Отправить изменения в GitHub (для разработчика)

GitHub **не принимает пароль аккаунта** для `git push` — нужен [Personal Access Token](https://github.com/settings/tokens) (права `repo`).

1. Создайте файл учётных данных (не коммитится):

```bash
cp .github-credentials.example .github-credentials
nano .github-credentials   # GITHUB_USER=matos85, GITHUB_TOKEN=ghp_...
```

2. Отправьте код:

```bash
chmod +x scripts/push-github.sh
./scripts/push-github.sh
```

Альтернатива — в терминале Cursor/Git сам запросит логин и token при `git push` (если remote на HTTPS):

```bash
git remote set-url origin https://github.com/matos85/grafana-bitrix-max-monitoring.git
git push -u origin main
# Username: matos85
# Password: <вставьте Personal Access Token, не пароль от GitHub>
```

---

## Секреты и чувствительные данные

### Главное правило

> **Пароли, ключи Bitrix и cookie-секреты — только в файле `.env` на сервере.**  
> В GitHub, в чатах и в скриншотах их быть не должно.

Файл `.env` создаётся **локально** из `.env.example` и **не коммитится**.

### Где что указывать

| Данные | Где указать | Пример переменной |
|--------|-------------|-------------------|
| IP/домен сервера мониторинга | `.env` | `HOST_IP`, `GRAFANA_ROOT_URL`, `OAUTH_BRIDGE_PUBLIC_URL` |
| Адреса портала Bitrix24 | `.env` | `BITRIX_AUTH_BASE_URL`, `BITRIX_REST_BASE`, `BITRIX_TOKEN_URL` |
| Redirect URI в кабинете Bitrix | Bitrix24 UI | `{OAUTH_BRIDGE_PUBLIC_URL}/oauth/callback` |
| Пароль админа Grafana | `.env` | `GF_ADMIN_PASSWORD` |
| Пароль viewer Grafana | `.env` | `GF_VIEWER_PASSWORD` |
| Пароли MySQL | `.env` | `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD` |
| Client ID / Secret Bitrix24 | `.env` | `OAUTH_BRIDGE_CLIENT_ID`, `OAUTH_BRIDGE_CLIENT_SECRET` |
| Секрет для cookies Prometheus OAuth | `.env` | `OAUTH2_PROXY_COOKIE_SECRET` (32 символа) |
| Ключ API для POST событий MAX | `.env` | `MAX_METRICS_API_KEY` (можно оставить пустым) |
| Приватный ключ JWT моста (редко) | `.env` или переменная окружения Docker | `OAUTH_BRIDGE_JWT_PRIVATE_KEY_PEM` — если не задан, ключ генерируется при каждом перезапуске моста |

Полный список переменных с комментариями — в файле **`.env.example`**.

### Обязательные переменные (проверяет `docker compose`)

Без этих ключей в `.env` запуск завершится ошибкой «задайте … в .env»:

| Группа | Переменные |
|--------|------------|
| Grafana | `GF_ADMIN_USER`, `GF_ADMIN_PASSWORD`, `GRAFANA_ROOT_URL`, `GRAFANA_DOMAIN` |
| OAuth / Bitrix | `OAUTH_BRIDGE_PUBLIC_URL`, `OAUTH_BRIDGE_CLIENT_ID`, `OAUTH_BRIDGE_CLIENT_SECRET`, `BITRIX_AUTH_BASE_URL`, `BITRIX_TOKEN_URL`, `BITRIX_REST_BASE` |
| Prometheus proxy | `PROMETHEUS_PUBLIC_URL`, `OAUTH2_PROXY_COOKIE_SECRET` |
| MySQL | `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD` |
| Локальный viewer | `GF_VIEWER_PASSWORD` (если используете `GF_VIEWER_USER`) |

Опционально: `HOST_IP`, `MAX_METRICS_API_KEY`, `BITRIX_CLIENT_ID` / `BITRIX_CLIENT_SECRET` (если отличаются от `OAUTH_BRIDGE_*`), порты `*_PORT`, `OAUTH_EMAIL_DOMAIN`.

### GitHub Secrets (когда нужны)

**GitHub → Repository → Settings → Secrets and variables → Actions**

Используйте **только если** настроите CI/CD (GitHub Actions), который сам деплоит на сервер. Тогда секреты кладут в **Secrets**, а в workflow передают на сервер при деплое — **не** хранят в коде.

Для обычной схемы «склонировал репозиторий на сервер и запустил docker compose» **GitHub Secrets не обязательны** — достаточно `.env` на сервере.

### Если секрет случайно попал в GitHub

1. Сразу **сменить** пароль / Client Secret в Bitrix24 и в `.env` на сервере.  
2. Удалить секрет из истории git (или сделать новый репозиторий) — старые коммиты на GitHub могут хранить утечку.  
3. Проверить, что `.env` в `.gitignore` и не в индексе: `git check-ignore -v .env`

### Безопасность в сети

Проект рассчитан на **внутреннюю сеть (LAN)**. Для доступа из интернета нужны HTTPS, файрвол и политики компании — это отдельная настройка (reverse proxy, сертификаты), в базовом README не разбирается.

---

## Частые проблемы

### «Не могу войти через Bitrix» / в профиле «bitrix_1234 (app)»

- В Bitrix24 не выдано право **Пользователи** — включите и переустановите приложение.  
- Проверьте Redirect URI: `{OAUTH_BRIDGE_PUBLIC_URL}/oauth/callback` (значение из `.env`)  
- Логи: `docker logs monitoring-oauth-bridge`

### Пустые графики MAX

1. Отправьте тестовый POST (см. выше).  
2. В Prometheus (внутри сети Docker) job `max-messenger` должен быть **UP**.  
3. Перезапустите Grafana после смены provisioning:  
   `docker compose --env-file .env up -d`

### Ошибка при запуске «задайте … в .env»

Запускайте **всегда** с файлом окружения:

```bash
docker compose --env-file .env up -d --build
```

И заполните обязательные поля в `.env` — см. таблицу [выше](#обязательные-переменные-проверяет-docker-compose).

### Сменили IP сервера

Обновите в `.env`: `HOST_IP`, `GRAFANA_ROOT_URL`, `OAUTH_BRIDGE_PUBLIC_URL`, `PROMETHEUS_PUBLIC_URL`, `GRAFANA_DOMAIN` и Redirect URI в Bitrix24, затем:

```bash
docker compose --env-file .env up -d --build
```

---

## Состав проекта (для администраторов)

| Папка / файл | Назначение |
|--------------|------------|
| `docker-compose.yml` | Контейнеры; URL и секреты только из `.env` (без зашитых IP) |
| `oauth-bridge/` | Мост OAuth Bitrix24 → Grafana / oauth2-proxy |
| `max-metrics/` | Приём событий MAX и метрики Prometheus |
| `prometheus/prometheus.yml` | Что и как часто опрашивать |
| `grafana/provisioning/` | Дашборды и источник данных Prometheus |
| `scripts/` | Инициализация пользователей Grafana, локаль ru-RU |
| `.env.example` | Шаблон настроек (безопасно для GitHub) |

---

## Дополнительно

- Русская локаль Grafana: образ 12.0.0 + `grafana/locales/ru-RU/`  
- Обновление перевода: `./scripts/download-grafana-locale.sh`  
- Генерация тестовых событий MAX: `scripts/generate-max-events.py`  
- Grafana хранит настройки в **MySQL**; при первом переходе с SQLite старые пользователи не переносятся автоматически.

---

## Лицензия и поддержка

Уточните у владельца репозитория политику использования. Вопросы по развёртыванию — в Issues на GitHub или внутренней службе поддержки вашей организации.
