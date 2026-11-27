import io
import json
import os
import unittest
from unittest.mock import patch

from app.agent import model_sources, numeric_checks, run_agent, workday_answer


class NumericCheckTests(unittest.TestCase):
    def test_strict_workday_threshold(self):
        sources = [{"quote": "Карточка отклоняется после ожидания более 5 рабочих дней."}]
        for days, expected in ((4, False), (5, False), (6, True)):
            with self.subTest(days=days):
                [check] = numeric_checks(f"Прошло {days} рабочих дней.", sources)
                self.assertEqual(check["выполнено"], expected)

    def test_inclusive_percent_threshold_and_decimal(self):
        sources = [{"quote": "Не более 30%: подключается SS. Более 30%: только поставка."}]
        for share, expected in (("29,5", [True, False]), ("30", [True, False]), ("30,1", [False, True])):
            with self.subTest(share=share):
                checks = numeric_checks(f"Доля проекта {share}%.", sources)
                self.assertEqual([check["выполнено"] for check in checks], expected)
                self.assertEqual([check["источник"] for check in checks], [1, 1])

    def test_skips_different_units_or_ambiguous_numbers(self):
        sources = [{"quote": "Более 5 рабочих дней."}]
        self.assertEqual(numeric_checks("Прошло 6 календарных дней.", sources), [])
        self.assertEqual(numeric_checks("Прошло 4 рабочих дня или 6 рабочих дней?", sources), [])

    def test_filters_only_the_complementary_branch_for_generation(self):
        original = "Если подходящего предложения нет:\na. Не более 30%: подключается SS.\nb. Более 30%: только поставка."
        sources = [{"quote": original}]
        [model_source] = model_sources(sources, numeric_checks("Доля 30%.", sources))
        self.assertIn("Если подходящего предложения нет", model_source["quote"])
        self.assertIn("подключается SS", model_source["quote"])
        self.assertNotIn("только поставка", model_source["quote"])
        self.assertEqual(sources[0]["quote"], original)

    def test_workday_answer_uses_calculation_and_keeps_full_rule(self):
        source = {"quote": "Если ответа нет более 5 рабочих дней, карточку отклоняют."}
        for days, expected in ((5, "не выполняется"), (6, "выполняется")):
            question = f"Ответа нет {days} рабочих дней."
            self.assertEqual(
                workday_answer(question, source, numeric_checks(question, [source])),
                (f"Условие «более 5 рабочих дней» при указанном сроке {expected}. {source['quote']}", False),
            )
        self.assertEqual(
            workday_answer("Ответа нет 6 календарных дней.", source, []),
            ("Сколько прошло рабочих дней? Регламент устанавливает срок в рабочих днях.", True),
        )


class QualityRoutingTests(unittest.TestCase):
    def test_missing_company_rule_is_sent_to_quality(self):
        question = "Сколько дней даётся на согласование ТКП с юристами?"
        history = [
            {"role": "user", "content": "ДБРЦТ, пресейл, нет анкеты", "status": None},
            {"role": "assistant", "content": "Заявка ждёт анкету.", "status": "answered"},
        ]
        decision = {
            "scope": "company_process",
            "status": "no_answer",
            "text": "В источниках нет срока согласования.",
            "source_ids": [],
        }
        response = {"message": {"content": json.dumps(decision)}}
        with patch.dict(os.environ, {"AGENT_SOURCES": "demo"}), patch(
            "app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
        ):
            result = run_agent(question, history)

        self.assertEqual(result["status"], "escalated")
        self.assertEqual(result["reason"], decision["text"])

    def test_personal_bonus_question_is_refused(self):
        decision = {
            "scope": "out_of_scope",
            "status": "no_answer",
            "text": "Не могу ответить на вопрос о премии.",
            "source_ids": [],
        }
        response = {"message": {"content": json.dumps(decision)}}
        with patch.dict(os.environ, {"AGENT_SOURCES": "demo"}), patch(
            "app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
        ):
            result = run_agent("Какая у меня будет премия за сделку?", [])

        self.assertEqual(result["status"], "refused")
        self.assertIn("руководителю", result["answer"])


if __name__ == "__main__":
    unittest.main()
