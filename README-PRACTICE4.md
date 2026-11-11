# Практика 4 Миграция Mattermost

Титов Георгий Павлович, БСБО-13-25.

Проект переносит Mattermost 9.5 и его постоянные данные из MySQL 8.0 в
PostgreSQL 15, затем запускает Mattermost 12.0.1. Ветка GitHub: `migration`.
Адрес приложения на Mac: http://localhost:8065.

## Файлы работы

- `legacy/docker-compose.yml` — исходное приложение и MySQL.
- `docker-compose.prod.yml` — обновлённое приложение и PostgreSQL.
- `.github/workflows/migration.yml` — автоматический перенос и повторное развёртывание.
- `scripts/migration.py` — резервное копирование, pgloader и проверка данных.
- `fixtures/expected-data.json` — исходные ID пяти сообщений и контрольные суммы файлов.
- `DEFENSE-PRACTICE4.md` — объяснения и вопросы для защиты.

В этой ветке также сохранены файлы предыдущей практики. Её `docker-compose.yml`
и workflow `deploy-test.yml` относятся к приложению на порту 8082. Для этой работы
нужно выбирать **Idempotent Mattermost Migration and Upgrade**.

## Повторный запуск в GitHub

1. Запустить Docker Desktop.
2. Если runner Offline, открыть Терминал и выполнить:

```bash
cd /Users/georgijtitov/Documents/Codex/github-actions-lesson/work/runner
./run.sh
```

3. В репозитории Cred0-WW/mainRepository открыть Actions, выбрать
   Idempotent Mattermost Migration and Upgrade, затем Run workflow и ветку migration.
4. Дождаться Success. При существующем маркере шаги резервного копирования
   и pgloader получают статус Skipped, а проверки выполняются снова.

Если GitHub не показывает кнопку Run workflow для файла, которого нет в
основной ветке main, повторный запуск можно вызвать пустым коммитом в migration:

```bash
git switch migration
git commit --allow-empty -m "Repeat Mattermost deployment"
git push origin migration
```

GitHub Secrets: `DB_MYSQL_ROOT_PASSWORD`, `DB_MYSQL_PASSWORD`,
`DB_POSTGRES_PASSWORD`, `MM_ADMIN_PASSWORD`. Значения в коде не хранятся.
Имя пользователя Mattermost: `georgiy.titov`.

## Где сохраняются данные

Оба Compose-файла используют один проект `mattermostpr4`, одну bridge-сеть
`mattermostpr4_network` и один том `mattermostpr4_data` для `/mattermost/data`.
Базы используют отдельные тома `mattermostpr4_mysql` и `mattermostpr4_postgres`.
Порт 8065 опубликован только на loopback; порты баз на Mac не публикуются.

Метка успешной миграции хранится вне checkout:
`~/Documents/Codex/mattermost-migration-lesson/state/.migration_completed`.
Поэтому очистка рабочей папки actions/checkout не удаляет результат миграции.
Путь можно переопределить переменной `MATTERMOST_STATE_DIR`.

Перед переносом приложение останавливается. Скрипт сохраняет согласованный
SQL-дамп MySQL и архив общего тома в `state/backups/<run-id>/` с правами 0600.
Эти резервные копии могут содержать закрытые данные и не загружаются в GitHub.
В артефакт попадает только манифест с размером и SHA256.

## Как устроен перенос

1. Проверяется постоянный маркер.
2. Если маркера нет, останавливается приложение и создаётся резервная копия.
3. Поднимается PostgreSQL. Только учебная целевая схема пересоздаётся, чтобы
   частично выполненную миграцию можно было повторить без дубликатов.
4. Команда `mattermost db migrate` версии 9.5 создаёт правильную PostgreSQL-схему
   исходной версии. Это сохраняет нужные типы, перечисления и историю миграций.
5. Официальный `mattermost/mattermost-pgloader:latest` переносит данные с
   опцией `WITH include drop` в конфигурации (эквивалент CLI `--with "include drop"`). Для заранее подготовленной схемы используется
   `data only`; фактическое очищение частичного результата обеспечивается
   явным пересозданием целевой схемы до pgloader. Исходная MySQL не очищается.
6. Восстанавливаются полнотекстовые индексы. Compose запускает 12.0.1 с
   `--remove-orphans`, удаляя старый контейнер MySQL в том же Compose-проекте.
7. Проверяются HTTP 200, версия сервера, сообщения с исходными ID, PostgreSQL,
   приватный канал, вложение, аватар и имя общего тома.
8. Только после этих проверок создаётся маркер успешной миграции.

Повторный успешный запуск не очищает PostgreSQL и не запускает pgloader.
Удалять маркер на работающем целевом стеке нельзя: это запрос на повторный
перенос из сохранённой MySQL и может отменить более новые изменения.

## Отличия от примера в методичке

Используется официальный образ `mattermost/mattermost-team-edition` и драйвер
`mysql`, исправлены имена переменных и shell-синтаксис. Linux-путь `/home/runner`
заменён постоянным путём на Mac. Вместо фиксированных пауз используются
healthcheck и опрос HTTP. Маркер создаётся после проверки результата.
Простой перенос схемы MySQL не гарантирует совместимость типов PostgreSQL,
поэтому перед pgloader создаётся схема самой исходной версией Mattermost.

Официальные образы Mattermost в этой работе используют linux/amd64; на Apple
Silicon указан `platform: linux/amd64`. MySQL и PostgreSQL работают нативно ARM64.

## Проверка вручную

```bash
docker ps --filter label=com.docker.compose.project=mattermostpr4
docker exec mattermost-postgres psql -U mmuser -d mattermost -c "SELECT id,message FROM posts WHERE type='' ORDER BY createat;"
curl -f http://localhost:8065/api/v4/system/ping
```

В проекте должны работать только `mattermost` и `mattermost-postgres`.
MySQL-том сохраняется для восстановления; `--remove-orphans` удаляет контейнер,
а не данные тома. Не выполнять `docker compose down -v` для демонстрационного стека.

## Источники

- https://docs.mattermost.com/deployment-guide/manual-postgres-migration.html
- https://docs.mattermost.com/deployment-guide/postgres-migration-assist-tool.html
- https://docs.docker.com/reference/cli/docker/compose/up/
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
