#!/usr/bin/env python3
"""Install only WeFlare tender v2 handlers into an existing bot.py.
Usage: python3 install_weflare_v2.py /root/bot.py --check
       python3 install_weflare_v2.py /root/bot.py
       python3 install_weflare_v2.py /root/bot.py --rollback
"""
import argparse
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

MODULE_SOURCE = '"""WeFlare tender flow v2. Standard library only; no token is stored here."""\nimport contextlib\nimport datetime\nimport json\nimport logging\nimport math\nimport os\nfrom pathlib import Path\nimport re\nimport secrets\nimport sqlite3\nimport time\nfrom urllib.parse import urlencode\n\nVERSION = "2026.10.02-v2"\nFORM_URL = "https://reliancetradinghelp-alt.github.io/welfare-tender/tender.html"\nBOT_USERNAME = "weflare_sup_bot"\nTERMS_VERSION = "wf-tender-2026-10-v2"\nTERMS_TEXT = (\n    "Я подтверждаю своё участие и согласен с условиями корпоративной квоты. "\n    "Настоящим я выражаю согласие на обработку моих персональных и корпоративных данных, "\n    "а также подтверждаю своё намерение участвовать в закрытых торгах WeFlare."\n)\nINVITATION_TTL = 24 * 60 * 60\nLOG = logging.getLogger("weflare.tender")\n\n\nclass TenderFlow:\n    def __init__(self, bot, active_chats=None, connections=None, db_path=None):\n        self.bot = bot\n        self.active_chats = active_chats if active_chats is not None else {}\n        self.connections = connections if connections is not None else {}\n        self.db_path = Path(db_path) if db_path else Path(__file__).with_name("weflare_tender.sqlite3")\n        if not self.db_path.exists():\n            fd = os.open(str(self.db_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n            os.close(fd)\n        with self._db() as db:\n            db.execute("""CREATE TABLE IF NOT EXISTS invitations (\n                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, business_id TEXT,\n                created_at INTEGER NOT NULL, accepted_at INTEGER,\n                application_id TEXT UNIQUE, submission_json TEXT,\n                business_notified INTEGER NOT NULL DEFAULT 0\n            )""")\n            db.execute("CREATE INDEX IF NOT EXISTS invitations_user ON invitations(user_id, created_at)")\n        os.chmod(str(self.db_path), 0o600)\n\n    @contextlib.contextmanager\n    def _db(self):\n        conn = sqlite3.connect(str(self.db_path), timeout=8)\n        conn.row_factory = sqlite3.Row\n        try:\n            with conn:\n                yield conn\n        finally:\n            conn.close()\n\n    @staticmethod\n    def _markup(data):\n        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))\n\n    def _new(self, user_id, business_id):\n        token = secrets.token_urlsafe(18)\n        with self._db() as db:\n            db.execute(\n                "INSERT INTO invitations(token,user_id,business_id,created_at) VALUES(?,?,?,?)",\n                (token, int(user_id), business_id, int(time.time())),\n            )\n        return token\n\n    def _get(self, token):\n        with self._db() as db:\n            row = db.execute("SELECT * FROM invitations WHERE token=?", (token,)).fetchone()\n        return dict(row) if row else None\n\n    def invite(self, message):\n        """Called only by the existing business-message tender branch."""\n        user_id = int(message.chat.id)\n        business_id = getattr(message, "business_connection_id", None)\n        token = self._new(user_id, business_id)\n        self.connections[user_id] = business_id\n        if user_id in self.active_chats:\n            self.active_chats[user_id].update(\n                last_activity=time.time(), last_sender="client", reminder_step=0\n            )\n        url = "https://t.me/" + BOT_USERNAME + "?start=tender_" + token\n        markup = {"inline_keyboard": [[{"text": "✦ Перейти к заявке", "url": url}]]}\n        kwargs = {"reply_markup": self._markup(markup), "parse_mode": "HTML"}\n        if business_id:\n            kwargs["business_connection_id"] = business_id\n        self.bot.send_message(\n            user_id,\n            "<b>WeFlare · Тендерная программа</b>\\n\\n"\n            "Оставьте подпись и подтвердите участие в форме.\\n"\n            "После отправки подтверждение появится в этом диалоге.",\n            **kwargs\n        )\n\n    def _keyboard(self, token):\n        url = FORM_URL + "?" + urlencode({\n            "v": VERSION, "source": "keyboard", "session": token\n        })\n        return self._markup({\n            "keyboard": [[{\n                "text": "✦ Открыть форму заявки",\n                "web_app": {"url": url}\n            }]],\n            "resize_keyboard": True,\n            "one_time_keyboard": False,\n            "is_persistent": True,\n            "input_field_placeholder": "Откройте форму кнопкой ниже"\n        })\n\n    def start(self, message):\n        """Handles /start, /start tender_<token>, /tender and tender words."""\n        raw = (getattr(message, "text", None) or "").strip()\n        parts = raw.split(maxsplit=1)\n        if not parts:\n            return False\n        command = parts[0].lower().split("@", 1)[0]\n        if command not in ("/start", "/tender", "тендер", "tender", "заявка"):\n            return False\n        user_id = int(message.chat.id)\n        if getattr(message.chat, "type", "private") != "private":\n            return False\n        payload = parts[1] if len(parts) == 2 else ""\n        token = None\n        if payload.startswith("tender_"):\n            candidate = payload[len("tender_"):]\n            row = self._get(candidate) if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", candidate) else None\n            if not row or row["user_id"] != user_id:\n                self.bot.send_message(user_id, "Эта ссылка недоступна. Попросите новую ссылку в чате поддержки.")\n                return True\n            if row["accepted_at"]:\n                self.bot.send_message(\n                    user_id, "Эта заявка уже получена: " + row["application_id"] +\n                    ". Для новой заявки отправьте /start."\n                )\n                return True\n            if time.time() - row["created_at"] > INVITATION_TTL:\n                token = self._new(user_id, row["business_id"])\n            else:\n                token = candidate\n        if token is None:\n            with self._db() as db:\n                row = db.execute(\n                    "SELECT * FROM invitations WHERE user_id=? AND accepted_at IS NULL "\n                    "AND created_at>=? ORDER BY created_at DESC,rowid DESC LIMIT 1",\n                    (user_id, int(time.time()) - INVITATION_TTL),\n                ).fetchone()\n            token = row["token"] if row else self._new(user_id, self.connections.get(user_id))\n        self.bot.send_message(\n            user_id,\n            "<b>Ваша заявка WeFlare</b>\\n\\n"\n            "Нажмите «✦ Открыть форму заявки» на клавиатуре под строкой ввода.\\n"\n            "Подпись → согласие → отправка.\\n\\n"\n            "Если клавиатура скрыта, нажмите значок клавиатуры рядом с полем сообщения "\n            "или отправьте /start ещё раз.",\n            reply_markup=self._keyboard(token), parse_mode="HTML"\n        )\n        return True\n\n    @staticmethod\n    def validate_signature(signature):\n        if not isinstance(signature, dict):\n            raise ValueError("Нет подписи.")\n        for key in ("width", "height"):\n            value = signature.get(key)\n            if type(value) is not int or not 1 <= value <= 4096:\n                raise ValueError("Некорректный размер подписи.")\n        strokes = signature.get("strokes")\n        if not isinstance(strokes, list) or not 1 <= len(strokes) <= 32:\n            raise ValueError("Некорректная подпись.")\n        total, distance = 0, 0.0\n        for stroke in strokes:\n            if not isinstance(stroke, list) or not stroke:\n                raise ValueError("Некорректный штрих.")\n            previous = None\n            for point in stroke:\n                if (not isinstance(point, list) or len(point) != 2 or\n                        any(type(v) is not int or not 0 <= v <= 1000 for v in point)):\n                    raise ValueError("Некорректные координаты подписи.")\n                total += 1\n                if total > 180:\n                    raise ValueError("Слишком сложная подпись.")\n                if previous is not None:\n                    distance += math.hypot(point[0] - previous[0], point[1] - previous[1])\n                previous = point\n        if distance < 35:\n            raise ValueError("Добавьте подпись, а не одиночную точку.")\n        return signature\n\n    def accept(self, user_id, raw):\n        """No network calls. Atomically validates and stores one submission."""\n        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:\n            raise ValueError("Превышен размер заявки.")\n        try:\n            payload = json.loads(raw)\n        except (ValueError, TypeError):\n            raise ValueError("Форма устарела. Отправьте /start и откройте её заново.")\n        if not isinstance(payload, dict) or payload.get("event") != "tender_signed" or payload.get("v") != 2:\n            raise ValueError("Форма устарела. Отправьте /start и откройте её заново.")\n        token = payload.get("session")\n        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token):\n            raise ValueError("Откройте форму кнопкой в чате бота.")\n        if payload.get("consent") is not True or payload.get("terms") != TERMS_VERSION:\n            raise ValueError("Подтвердите актуальные условия участия.")\n        signature = self.validate_signature(payload.get("signature"))\n        now = int(time.time())\n        with self._db() as db:\n            db.execute("BEGIN IMMEDIATE")\n            row = db.execute("SELECT * FROM invitations WHERE token=?", (token,)).fetchone()\n            if not row or row["user_id"] != int(user_id):\n                raise ValueError("Ссылка не относится к вашему аккаунту. Запросите новую в чате поддержки.")\n            if row["accepted_at"]:\n                return dict(row), True\n            if now - row["created_at"] > INVITATION_TTL:\n                raise ValueError("Срок действия формы истёк. Отправьте /start и подпишите новую заявку.")\n            application_id = "WF-" + datetime.datetime.now(datetime.timezone.utc).strftime("%y%m%d") + "-" + token[:8].upper()\n            record = {\n                "version": 2, "consent": True, "terms_version": TERMS_VERSION,\n                "terms_text": TERMS_TEXT, "signature": signature,\n                "received_at_utc": now, "telegram_user_id": int(user_id)\n            }\n            db.execute(\n                "UPDATE invitations SET accepted_at=?,application_id=?,submission_json=? WHERE token=?",\n                (now, application_id, json.dumps(record, ensure_ascii=False, separators=(",", ":")), token),\n            )\n            result = dict(db.execute("SELECT * FROM invitations WHERE token=?", (token,)).fetchone())\n        return result, False\n\n    def _notify_business(self, row):\n        if not row["business_id"] or row["business_notified"]:\n            return\n        try:\n            self.bot.send_message(\n                row["user_id"],\n                "✅ Заявка на тендер получена.\\n\\n"\n                "Номер: " + row["application_id"] + "\\n"\n                "Подпись и подтверждение участия сохранены.\\n"\n                "С вами свяжется персональный менеджер.",\n                business_connection_id=row["business_id"]\n            )\n            with self._db() as db:\n                db.execute("UPDATE invitations SET business_notified=1 WHERE token=?", (row["token"],))\n        except Exception as error:\n            # Keep the saved submission; retry on a repeated delivery.\n            LOG.warning("Tender %s saved; business notification failed (%s).",\n                        row["application_id"], type(error).__name__)\n\n    def receive(self, message):\n        if getattr(message.chat, "type", "private") != "private":\n            return\n        user_id = int(message.chat.id)\n        sender = getattr(message, "from_user", None)\n        if sender is not None and int(sender.id) != user_id:\n            return\n        data = getattr(getattr(message, "web_app_data", None), "data", None)\n        try:\n            row, duplicate = self.accept(user_id, data)\n        except ValueError as error:\n            self.bot.send_message(user_id, str(error))\n            return\n        self._notify_business(row)\n        prefix = "Заявка уже была получена" if duplicate else "Заявка получена"\n        self.bot.send_message(\n            user_id,\n            "✅ " + prefix + ": " + row["application_id"] + "\\n\\n"\n            "Подпись и подтверждение участия сохранены.\\n"\n            "Для новой заявки отправьте /start.",\n            reply_markup=self._markup({"remove_keyboard": True})\n        )\n\n'
MARKER = "# WEFLARE_TENDER_V2_BEGIN"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(source):
    if MARKER in source:
        return None
    tree = ast.parse(source)
    names = ("handle_business_message", "handle_start_dm", "handle_webapp_data")
    functions = {}
    for name in names:
        found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
        if len(found) != 1:
            raise ValueError("Ожидалась одна функция " + name + ". Файл не изменён.")
        functions[name] = found[0]
    for name in ("bot", "active_chats", "tender_connections"):
        assigned = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets) for n in tree.body)
        if not assigned:
            raise ValueError("Не найдена переменная " + name + ". Файл не изменён.")
    branches = []
    for node in functions["handle_business_message"].body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "text_lower" and len(test.ops) == 1
                and isinstance(test.ops[0], ast.In)):
            try:
                values = ast.literal_eval(test.comparators[0])
            except (ValueError, TypeError):
                continue
            if isinstance(values, (list, tuple, set)) and "тендер" in values and "b2b" in values:
                branches.append(node)
    if len(branches) != 1:
        raise ValueError("Не найден однозначный блок тендера. Файл не изменён.")
    start = functions["handle_start_dm"]
    if not start.decorator_list:
        raise ValueError("Нет декоратора обработчика личных сообщений. Файл не изменён.")
    lines = source.splitlines(keepends=True)
    branch = branches[0]
    old_condition = ast.get_source_segment(source, branch.test)
    indent = " " * branch.col_offset
    replacement = indent + "if " + old_condition + ":\n" + indent + "    _wf_tender_v2.invite(message)\n" + indent + "    return\n"
    edits = [(branch.lineno - 1, branch.end_lineno, replacement)]
    for name, method in (("handle_start_dm", "start"), ("handle_webapp_data", "receive")):
        node = functions[name]
        if len(node.args.args) != 1 or node.args.args[0].arg != "message":
            raise ValueError("Неожиданная сигнатура " + name + ". Файл не изменён.")
        edits.append((node.lineno - 1, node.end_lineno,
                      "def " + name + "(message):\n    _wf_tender_v2." + method + "(message)\n"))
    position = min(n.lineno for n in start.decorator_list) - 1
    injection = (
        MARKER + "\n"
        "from weflare_tender import TenderFlow as _WeFlareTenderFlow\n"
        "_wf_tender_v2 = _WeFlareTenderFlow(bot, active_chats, tender_connections)\n"
        "# WEFLARE_TENDER_V2_END\n\n"
    )
    edits.append((position, position, injection))
    for left, right, value in sorted(edits, reverse=True):
        lines[left:right] = [value]
    result = "".join(lines)
    compile(result, "bot.py", "exec")
    compile(MODULE_SOURCE, "weflare_tender.py", "exec")
    return result


def rollback(target):
    manifest_path = target.parent / "weflare_v2_install.json"
    if not manifest_path.exists():
        raise ValueError("Нет записи об установке. Автоматический откат недоступен.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    module = target.parent / "weflare_tender.py"
    if str(target) != manifest["target"]:
        raise ValueError("Запись об установке относится к другому bot.py.")
    if digest(target.read_bytes()) != manifest["installed_bot_sha256"]:
        raise ValueError("После установки bot.py менялся. Откат остановлен, чтобы не потерять новые правки.")
    if not module.exists() or digest(module.read_bytes()) != manifest["installed_module_sha256"]:
        raise ValueError("Модуль после установки менялся. Автоматический откат остановлен.")
    backup = Path(manifest["backup_directory"])
    atomic_write(target, (backup / "bot.py").read_bytes(), manifest["bot_mode"])
    if manifest["module_existed"]:
        atomic_write(module, (backup / "weflare_tender.py").read_bytes(), manifest["module_mode"])
    else:
        module.unlink()
    manifest_path.rename(backup / "rolled_back_install.json")
    print("ОТКАТ ВЫПОЛНЕН. Исходный bot.py восстановлен.")
    print("База заявок сохранена. Перезапустите тот же процесс бота.")


def install(target, check=False):
    if not target.is_file():
        raise ValueError("Файл не найден: " + str(target))
    original = target.read_bytes()
    try:
        source = original.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Ожидался исходный bot.py в UTF-8.")
    result = prepare(source)
    module = target.parent / "weflare_tender.py"
    if result is None:
        if not module.is_file():
            raise ValueError("Маркер установки есть, но weflare_tender.py отсутствует.")
        print("Версия v2 уже подключена. Повторная установка не нужна.")
        return
    if check:
        print("ПРОВЕРКА ПРОЙДЕНА. Найдены 3 нужных участка. Ничего не изменено.")
        print("Будут обновлены: переход в тендер, /start, получение заявки.")
        print("Рабочий токен, камеры, напоминания и оценки останутся в исходном файле.")
        return
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = target.parent / "weflare_backups" / timestamp
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    shutil.copy2(str(target), str(backup / "bot.py"))
    bot_mode = stat.S_IMODE(target.stat().st_mode)
    module_existed = module.exists()
    module_mode = stat.S_IMODE(module.stat().st_mode) if module_existed else 0o600
    if module_existed:
        shutil.copy2(str(module), str(backup / "weflare_tender.py"))
    new_bot = result.encode("utf-8")
    new_module = MODULE_SOURCE.encode("utf-8")
    manifest = {
        "target": str(target), "backup_directory": str(backup),
        "original_bot_sha256": digest(original),
        "installed_bot_sha256": digest(new_bot),
        "installed_module_sha256": digest(new_module),
        "module_existed": module_existed, "module_mode": module_mode, "bot_mode": bot_mode
    }
    try:
        atomic_write(module, new_module, module_mode)
        atomic_write(target, new_bot, bot_mode)
        atomic_write(target.parent / "weflare_v2_install.json",
                     json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        atomic_write(target, original, bot_mode)
        if module_existed:
            atomic_write(module, (backup / "weflare_tender.py").read_bytes(), module_mode)
        elif module.exists():
            module.unlink()
        raise
    print("УСТАНОВЛЕНО. Код проверен на синтаксис.")
    print("Резервная копия: " + str(backup / "bot.py"))
    print("Добавлен модуль: " + str(module))
    print("Перезапустите СУЩЕСТВУЮЩИЙ процесс бота, затем отправьте /start.")
    print("Установщик не запускал и не останавливал ваш бот.")


def main():
    parser = argparse.ArgumentParser(description="WeFlare tender v2 installer")
    parser.add_argument("bot_file", nargs="?", default="/root/bot.py")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Only check; do not write")
    group.add_argument("--rollback", action="store_true", help="Restore the last installed version")
    args = parser.parse_args()
    target = Path(args.bot_file).expanduser().resolve()
    try:
        if args.rollback:
            rollback(target)
        else:
            install(target, args.check)
    except Exception as error:
        print("ОСТАНОВЛЕНО: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

