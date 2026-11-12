# Практика 5 Debian пакет syspulse и Nexus APT

Титов Георгий Павлович, БСБО-13-25.

Ветка `release` содержит полный цикл сборки Go-программы syspulse версии 1.2.0,
упаковки в Debian-пакет, публикации в hosted APT-репозиторий Nexus и установки
через apt в новом контейнере Ubuntu 22.04 amd64. Предыдущие практики сохранены
в других ветках и в исходных файлах репозитория.

## Что открыть

- Nexus: http://localhost:8081
- Репозиторий пакетов с Mac: http://localhost:8081/repository/syspulse/
- Внутри Docker-сети: http://nexus:8081/repository/syspulse/
- Workflow: `.github/workflows/publish-deb.yml`
- Приложение: `main.go`
- Сборка и публикация: `packaging/` и `scripts/package_ci.py`
- Вопросы для защиты: `DEFENSE-PRACTICE5.md`

Ветка release создана от main. Файлы предыдущего приложения в корне сохранены;
для этой практики используется `docker-compose.nexus.yml`, а не старый
`docker-compose.yml`. Docker-проект Nexus называется `syspulsepr5`.

## Окружение

Nexus 3.96.5 работает в ARM64-контейнере с постоянным томом
`syspulsepr5_nexus_data` и отдельной bridge-сетью `syspulsepr5_network`.
Веб-порт опубликован только на 127.0.0.1:8081.

Сам runner работает на Mac. Go 1.27.4, dpkg-deb, curl и GnuPG запускаются
в Linux-контейнере `syspulse-practice5-tools:1.27.4`. Go кросс-компилирует
статический бинарник под linux/amd64 с `CGO_ENABLED=0`. Наличие динамического
загрузчика PT_INTERP в ELF проверяется автоматически и считается ошибкой.

Версия Debian-пакета извлекается из `const Version` в main.go. Она не задаётся
второй независимой константой. Пакет содержит `/usr/bin/syspulse` с правами 0755
и владельцем root. Depends на libc6 не добавлен: статическому Go-бинарнику
не нужна динамическая libc.

## Nexus и подпись

Hosted APT-репозиторий `syspulse` использует distribution `stable`, component
`main` и writePolicy `ALLOW`, разрешающий повторную публикацию той же версии.
Publisher имеет только repository-scoped права add, edit, read и browse.
Администратор Nexus не используется в GitHub Actions.

Метаданные репозитория подписываются отдельным GPG-ключом. В проекте хранится
только публичный `packaging/syspulse-archive-keyring.gpg`. Закрытый ключ остаётся
локально и в конфигурации Nexus. Ubuntu подключает репозиторий через signed-by;
проверка подписи не отключается параметром trusted=yes.

HTTP используется только для локального учебного стенда. Подпись APT обеспечивает
проверку подписанных метаданных и контрольных сумм загружаемого пакета.

## Workflow

1. Получить код из release.
2. Подготовить контейнер с Go, dpkg-deb и curl.
3. Создать временный файл окружения из GitHub Secrets.
4. Собрать и проверить статический ELF64 amd64-бинарник.
5. Создать DEBIAN/control и .deb с версией из main.go.
6. Выполнить HTTP POST Debian-пакета в корень hosted APT-репозитория.
7. Проверить InRelease и SHA256 пакета в Packages.
8. В новом Ubuntu-контейнере выполнить apt-get update и apt-get install,
   проверить dpkg-query, syspulse --version и SHA256 установленного бинарника.
9. Загрузить .deb и очищенные результаты проверок как GitHub Artifact.
10. Удалить только созданный этим запуском временный файл учётных данных.

Secrets: `NEXUS_HOST`, `NEXUS_USER`, `NEXUS_PASSWORD`. Для контейнеров значение
NEXUS_HOST указывает на имя Nexus в общей Docker-сети. Реальные пароли в коде,
логах и архиве для сдачи не хранятся.

Для одинакового исходного кода SOURCE_DATE_EPOCH берётся из времени последнего
коммита, изменившего main.go. Используются trimpath, buildvcs=false и фиксированная
версия Go. Пустой коммит повторного запуска не меняет исходную версию программы.

## Повторный запуск

Docker Desktop и Nexus должны работать. Если runner Offline:

```bash
cd /Users/georgijtitov/Documents/Codex/github-actions-lesson/work/runner
./run.sh
```

Workflow запускается при push в release. Для повторной проверки:

```bash
git switch release
git commit --allow-empty -m "Repeat syspulse publication and clean APT verification"
git push origin release
```

В YAML также предусмотрен workflow_dispatch. Если GitHub не показывает кнопку
Run workflow из-за отсутствия этого workflow в основной ветке, используйте push.

Повторный запуск снова публикует пакет с той же версией. Nexus разрешает redeploy,
а проверка всегда создаёт новый Ubuntu-контейнер без заранее установленного
syspulse. Локальный .deb в этот контейнер не монтируется: установка идёт из APT.

## Локальная демонстрация

Открыть `syspulse-practice5.code-workspace` в VS Code. Задача по умолчанию
повторно собирает пакет и проверяет установку из Nexus. Пароль издателя для
чтения APT не требуется. Nexus должен быть уже настроен, а пакет опубликован.

Программа читает `/proc` внутри Linux-контейнера. Это сведения Linux-окружения
Docker, а не macOS. Команда `syspulse --version` должна вывести `syspulse 1.2.0`.

## Отличия от учебного шаблона

- dpkg-deb выполняется в Linux, потому что runner установлен на macOS.
- Версия извлекается Python без GNU grep -P, которого нет в штатном macOS grep.
- Загрузка выполнена HTTP POST в корень APT-репозитория по документации Sonatype.
- Политика повторной публикации задаётся writePolicy ALLOW. Поля Maven
  Snapshot/Release не используются как настройки hosted APT.
- Nexus требует ключ для подписи APT-метаданных; клиент использует signed-by.
- Убраны интерактивные флаги -it, чтобы проверка работала в CI без TTY.

## Источники

- https://help.sonatype.com/en/apt-repositories.html
- https://github.com/sonatype/docker-nexus3
- https://go.dev/dl/
- https://manpages.debian.org/bookworm/dpkg/dpkg-deb.1.en.html
