import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import main


class DemoAccountTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {
            "KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"),
            "PUNKT_TEST_MAIL": "0",
            "PUNKT_EMPLOYEE_PASSWORD": "staff-test-secret",
            "PUNKT_QUALITY_PASSWORD": "quality-test-secret",
        }))
        self.enterContext(patch.object(main, "tokens", {}))

    def test_role_password_and_logout(self):
        self.assertTrue(all(account["password_required"] for account in main.users()))
        with self.assertRaises(HTTPException) as denied:
            main.login(main.LoginRequest(user_id="quality", password="staff-test-secret"))
        self.assertEqual(denied.exception.status_code, 401)
        token = main.login(main.LoginRequest(user_id="quality", password="quality-test-secret"))["token"]
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        self.assertEqual(main.current_user(credentials)["role"], "quality")
        main.logout(credentials)
        with self.assertRaises(HTTPException) as signed_out:
            main.current_user(credentials)
        self.assertEqual(signed_out.exception.status_code, 401)

    def test_statement_history_and_access_are_scoped(self):
        async def initialize():
            async with main.lifespan(main.app):
                pass

        asyncio.run(initialize())
        employee, quality = main.USERS["employee"], main.USERS["quality"]
        session = main.create_session(employee)["session_id"]
        main.create_session(quality)
        with main.database() as db:
            db.executemany(
                "INSERT INTO messages (session_id, message_id, role, content) VALUES (?, ?, 'user', ?)",
                [(session, "one", "Первый вопрос"), (session, "two", "Второй вопрос")],
            )
        [history] = main.sessions(employee)
        self.assertEqual((history["session_id"], history["title"]), (session, "Первый вопрос"))
        self.assertIsNone(main.sessions(quality)[0]["title"])
        with self.assertRaises(HTTPException) as hidden:
            main.session_messages(session, quality)
        self.assertEqual(hidden.exception.status_code, 404)
        with self.assertRaises(HTTPException) as denied:
            main.require_quality(employee)
        self.assertEqual(denied.exception.status_code, 403)

    def test_partial_or_shared_password_configuration_fails(self):
        for invalid_password in ("", os.environ["PUNKT_EMPLOYEE_PASSWORD"]):
            with self.subTest(invalid_password=invalid_password), patch.dict(
                os.environ, {"PUNKT_QUALITY_PASSWORD": invalid_password}
            ), self.assertRaises(RuntimeError):
                main.validate_account_passwords()
