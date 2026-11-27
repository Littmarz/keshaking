import io
import json
import os
import unittest
from unittest.mock import patch

from app.agent import (
    AgentError,
    closure_deadline_answer,
    closure_deadline_sources,
    conflicting_reply_deadlines,
    direct_prohibition,
    following_recovery_step,
    model_sources,
    numeric_checks,
    run_agent,
    same_quality_question,
    search_documents,
    shared_rule,
    workday_answer,
)
from retrieval.mock import Chunk, Hit, SearchResult


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
        original = "2. Если подходящего предложения нет:\na. Не более 30%: подключается SS.\nb. Более 30%: только поставка."
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
    def test_quality_match_is_compared_with_regulation_and_conflict_uses_the_rule(self):
        question = "Кто проверяет заявку в Техдирекции?"
        chunk = Chunk("tech::1.2", "tech", "Техническая дирекция", "1.2",
                      "Заявку проверяет координатор.", directorate="Техдирекция")
        entry = {
            "qa_entry_id": "qa-demo", "ticket_id": "Q-0001", "question": question,
            "answer": "Заявку проверяет автор.", "doc_id": "tech", "section": "1.2",
            "directorate": "Техдирекция", "stage": None, "status": "confirmed",
        }
        decision = {"scope": "company_process", "status": "conflict",
                    "text": "Применяйте ответ эксперта: заявку проверяет автор.", "source_ids": [2, 1]}
        response = {"message": {"content": json.dumps(decision)}}
        with patch.dict(os.environ, {"AGENT_SOURCES": "documents"}), patch(
            "app.agent.search_documents", return_value=(SearchResult(hits=[Hit(chunk, 1.0)]), [chunk])
        ), patch("app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as ollama:
            result = run_agent(question, [], [entry])

        context = json.loads(json.loads(ollama.call_args.args[0].data)["messages"][-1]["content"])
        self.assertEqual([source["source_type"] for source in context["sources"]], ["regulation", "qa_entry"])
        self.assertEqual(context["matching_quality_source_ids"], [2])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual([source["source_type"] for source in result["citations"]], ["regulation", "qa_entry"])
        self.assertIn(chunk.text, result["answer"])
        self.assertNotIn(entry["answer"], result["answer"])

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

    def test_published_duration_answer_matches_paraphrase_and_keeps_conditions(self):
        question = "Сколько дней даётся на согласование ТКП с юристами?"
        self.assertTrue(same_quality_question("За сколько дней юристы согласуют ТКП?", question))
        self.assertFalse(same_quality_question("Сколько дней даётся на согласование ТКП без юристов?", question))
        self.assertFalse(same_quality_question("За сколько часов юристы согласуют ТКП?", question))
        self.assertFalse(same_quality_question("За сколько дней бухгалтерия согласует ТКП?", question))

    def test_missing_work_policy_routes_to_quality_even_if_model_refuses(self):
        decision = {"scope": "out_of_scope", "status": "no_answer", "text": "Нет данных.", "source_ids": []}
        raw = json.dumps({"message": {"content": json.dumps(decision)}}).encode()
        cases = (
            ("Как меняется лицензия при реорганизации юрлица?", "escalated"),
            ("Какие требования к одежде на переговорах с заказчиком?", "escalated"),
            ("Какая у меня будет премия за сделку с заказчиком?", "refused"),
        )
        for question, expected in cases:
            with self.subTest(question=question), patch.dict(os.environ, {"AGENT_SOURCES": "documents"}), patch(
                "app.agent.search_documents", return_value=(SearchResult(hits=[]), [])
            ), patch("app.agent.urlopen", return_value=io.BytesIO(raw)):
                self.assertEqual(run_agent(question, [])["status"], expected)


class DialogTests(unittest.TestCase):
    def test_permission_search_recovers_a_short_rule_for_an_abbreviated_role(self):
        long_clause = Chunk(
            "doc::2.1", "doc", "Регламент", "2.1",
            "Руководитель проекта ведёт план проекта и назначает исполнителей. "
            "Администратор проекта помогает готовить и хранить отчётность. Итоги отчётного "
            "периода вносятся в систему и передаются заинтересованным сотрудникам.",
        )
        short_clause = Chunk(
            "doc::2.6", "doc", "Регламент", "2.6",
            "Администратора проекта назначают только на проекты, не требующие управления рисками. "
            "В остальных случаях назначают руководителя проекта.",
        )
        other_hits = [
            Hit(Chunk(f"doc::{i}.1", "doc", "Регламент", f"{i}.1",
                      "Ведёт карточку проекта и формирует отчётность."), 0.6)
            for i in range(3, 7)
        ]
        result = SearchResult(hits=[Hit(long_clause, 0.8), *other_hits, Hit(short_clause, 0.4)], scope=["ИБ"])
        with patch("app.agent.document_corpus_available", return_value=True), patch(
            "retrieval.mock.search", return_value=result
        ), patch("retrieval.mock.get_section", return_value=None):
            _, chunks = search_documents("Можно назначить АП вместо руководителя проекта в ИБ?", [])

        self.assertEqual(chunks[0], short_clause)

    def test_closure_deadlines_keep_the_two_applicable_regulation_clauses(self):
        first = Chunk(
            "workflow::2.1", "workflow", "Порядок работы", "2.1",
            "Заявку в статусе «Ожидает сведения» закрывают, если она не обновлялась более 3 "
            "рабочих дней. Устанавливается статус «Дисквалифицировано».",
        )
        related = Chunk(
            "workflow::5.3", "workflow", "Порядок работы", "5.3",
            "Заявки другого подразделения сразу закрывают со статусом «Дисквалифицировано». "
            "Специалист устанавливает статус заявки в «Ожидает сведения». Если данные "
            "не поступили в течение 3 рабочих дней с момента создания заявки, "
            "специалист закрывает её со статусом «Дисквалифицировано».",
        )
        other_outcome = Chunk(
            "workflow::5.4", "workflow", "Порядок работы", "5.4",
            related.text.replace("Дисквалифицировано", "Выполнено"),
        )
        other_document = Chunk("other::5.3", "other", "Другой порядок", "5.3", related.text)
        chunks = [first, other_outcome, other_document, related]

        self.assertEqual(closure_deadline_sources("Через сколько её закроют?", chunks), [first, related])
        self.assertEqual(closure_deadline_sources("Кто закроет заявку?", chunks), [])
        answer = closure_deadline_answer([first, related])
        self.assertIn("не обновлялась более 3 рабочих дней", answer)
        self.assertIn("в течение 3 рабочих дней с момента создания заявки", answer)
        self.assertNotIn("другого подразделения", answer)

    def test_direct_prohibition_uses_the_matching_rule_and_defers_competing_rules(self):
        question = "Можно дать подрядчику доступ к порталу проекта ИБ?"
        unrelated = {"quote": "Координатор размещает подписанные документы на портале проекта."}
        rule = {"quote": "4.3. Запрещается предоставлять подрядчику доступ к порталу проекта."}
        alternative = {"quote": "4.4. Подрядчику разрешается предоставлять доступ к порталу проекта."}

        self.assertEqual(
            direct_prohibition(question, [unrelated, rule]),
            ("Запрещается предоставлять подрядчику доступ к порталу проекта.", [2]),
        )
        self.assertIsNone(direct_prohibition(question, [unrelated, rule, alternative]))
        self.assertIsNone(direct_prohibition(question + " Если он приглашён координатором?", [rule]))

    def test_permission_answer_preserves_every_exception_from_the_cited_rule(self):
        rule = Chunk(
            "doc::1.2", "doc", "Регламент", "1.2",
            "1.2. Координатору запрещается отправлять отчёт без согласования, "
            "кроме предварительного просмотра и учебных проектов.",
        )
        related = Chunk(
            "doc::2.1", "doc", "Регламент", "2.1",
            "2.1. Координатор регистрирует отправленные отчёты.",
        )
        decision = {"scope": "company_process", "status": "answered",
                    "text": "Исключение — учебные проекты.", "source_ids": [1, 2]}
        response = {"message": {"content": json.dumps(decision)}}
        with patch.dict(os.environ, {"AGENT_SOURCES": "documents"}), patch(
            "app.agent.search_documents", return_value=(SearchResult(hits=[], scope=["ИБ"]), [rule, related])
        ), patch("app.agent.direct_prohibition", return_value=None), patch(
            "app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
        ):
            answer = run_agent("Может ли координатор отправлять отчёты без согласования в ИБ?", [])

        self.assertIn("кроме предварительного просмотра и учебных проектов", answer["answer"])
        self.assertEqual([source["chunk_id"] for source in answer["citations"]], [rule.chunk_id])

    def test_model_receives_original_question_and_all_clarifications(self):
        history = [
            {"role": "user", "content": "Сколько дней дают на расчёт?", "status": None},
            {"role": "assistant", "content": "Какая дирекция?", "status": "needs_clarification"},
            {"role": "user", "content": "ДБРЦТ", "status": None},
            {"role": "assistant", "content": "Какая оценка нужна?", "status": "needs_clarification"},
        ]
        decision = {"scope": "company_process", "status": "answered", "text": "Ответ", "source_ids": [1]}
        response = {"message": {"content": json.dumps(decision)}}
        with patch.dict(os.environ, {"AGENT_SOURCES": "demo"}), patch(
            "app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
        ) as ollama:
            run_agent("Точная оценка", history)

        request = json.loads(ollama.call_args.args[0].data)
        model_input = json.loads(request["messages"][-1]["content"])
        self.assertEqual(model_input["question"], history[0]["content"])
        self.assertEqual(model_input["clarifications"], [
            {"preceding_question": "Какая дирекция?", "reply": "ДБРЦТ"},
            {"preceding_question": "Какая оценка нужна?", "reply": "Точная оценка"},
        ])

    def test_answer_from_only_one_plausible_directorate_requires_clarification(self):
        result = SearchResult(hits=[], ambiguous_scope=True, scope_options=[
            {"directorate": "ДБРЦТ"}, {"directorate": "Техдирекция"},
        ])
        chunks = [
            Chunk("dbrct::1.2", "dbrct", "ДБРЦТ", "1.2", "Срок — два дня.", directorate="ДБРЦТ"),
            Chunk("tech::1.2", "tech", "Техническая дирекция", "1.2", "Срок — пять дней.",
                  directorate="Техдирекция"),
        ]
        decision = {"scope": "company_process", "status": "answered", "text": "Два дня.", "source_ids": [1]}
        response = {"message": {"content": json.dumps(decision)}}
        with patch.dict(os.environ, {"AGENT_SOURCES": "documents"}), patch(
            "app.agent.search_documents", return_value=(result, chunks)
        ), patch("app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
            answer = run_agent("Сколько дней на заявку?", [])

        self.assertEqual(answer["status"], "needs_clarification")
        self.assertIn("дирекции", answer["clarification"]["question"])
        self.assertEqual(answer["citations"], [])

    def test_shared_rule_preserves_negation_and_recognizes_role_aliases(self):
        question = "Кто отвечает за проверку заявки?"
        first = {"directorate": "ИБ", "quote": "За проверку заявки отвечает АМ."}
        second = {"directorate": "Техдирекция", "quote": "За проверку заявки отвечает Аккаунт-менеджер."}
        self.assertTrue(shared_rule(question, [first, second]))
        negated = dict(second, quote=second["quote"].replace("отвечает", "не отвечает"))
        self.assertFalse(shared_rule(question, [first, negated]))

    def test_direct_procedure_returns_the_verified_clause_without_ollama(self):
        chunk = Chunk("example::1.2", "example", "Порядок работы", "1.2",
                      "1.2. Для создания спецификации АМ создаёт заявку на расчёт в CRM.",
                      directorate="Техдирекция")
        result = SearchResult(hits=[Hit(chunk, 1.0)], top_score=1.0, scope=["Техдирекция"])
        with patch.dict(os.environ, {"AGENT_SOURCES": "documents"}), patch(
            "app.agent.search_documents", return_value=(result, [chunk])
        ), patch("app.agent.urlopen") as ollama:
            answer = run_agent("Как в Техдирекции создать спецификацию?", [])

        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["citations"][0]["chunk_id"], chunk.chunk_id)
        self.assertIn("заявку на расчёт в CRM", answer["answer"])
        ollama.assert_not_called()

    def test_missing_document_from_clarification_selects_exact_condition(self):
        chunk = Chunk("example::1.2", "example", "Порядок работы", "1.2",
                      "1.2. При отсутствии квалификационной анкеты Архитектор ожидает её предоставления.",
                      directorate="ДБРЦТ")
        result = SearchResult(hits=[Hit(chunk, 0.5)], top_score=0.5, scope=["ДБРЦТ"])
        history = [
            {"role": "user", "content": "Что будет, если не хватает информации?", "status": None},
            {"role": "assistant", "content": "Какая дирекция?", "status": "needs_clarification"},
        ]
        with patch.dict(os.environ, {"AGENT_SOURCES": "documents"}), patch(
            "app.agent.search_documents", return_value=(result, [chunk])
        ), patch("app.agent.urlopen") as ollama:
            answer = run_agent("ДБРЦТ, нет квалификационной анкеты", history)

        self.assertEqual(answer["status"], "answered")
        self.assertIn("Архитектор ожидает", answer["answer"])
        ollama.assert_not_called()

    def test_recovery_step_follows_the_status_transition_in_the_same_regulation(self):
        deadline = {
            "doc_id": "a", "section": "2.3", "source_type": "regulation", "directorate": "ИБ", "stage": "проект",
            "quote": "Если РП не отвечает более 5 рабочих дней, карточка переводится в статус «Закрыта».",
        }
        recovery = dict(deadline, section="2.4", quote="2.4. Чтобы вернуть карточку в работу, смените статус на «Новый».")
        result = following_recovery_step("Что будет, если РП не ответит?", [deadline, recovery], [deadline])
        self.assertEqual(result, (recovery, "Чтобы вернуть карточку в работу, смените статус на «Новый»."))
        self.assertIsNone(following_recovery_step("Через сколько дней закроют?", [deadline, recovery], [deadline]))
        self.assertIsNone(following_recovery_step("Что будет?", [deadline, dict(recovery, doc_id="b")], [deadline]))

    def test_conflict_requires_two_distinct_verified_sources(self):
        for source_ids in ([1, 2], [1, 1], [1, 999]):
            with self.subTest(source_ids=source_ids):
                decision = {"scope": "company_process", "status": "conflict", "text": "Сроки расходятся.",
                            "source_ids": source_ids}
                response = {"message": {"content": json.dumps(decision)}}
                with patch.dict(os.environ, {"AGENT_SOURCES": "demo"}), patch(
                    "app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
                ):
                    if source_ids == [1, 2]:
                        result = run_agent("Какой срок?", [])
                        self.assertEqual(result["status"], "conflict")
                        self.assertEqual(len(result["citations"]), 2)
                        self.assertIsNone(result["ticket"])
                    else:
                        with self.assertRaises(AgentError):
                            run_agent("Какой срок?", [])

    def test_model_conflict_preserves_source_deadlines_and_labels(self):
        question = "Через сколько дней карточка проекта ИБ уходит в «Отклонён», если SS не отвечает?"
        rule = "Если SS не отвечает {}, карточка переводится в статус «Отклонён»."
        first = Chunk("ib-project::2.3", "ib-project", "Регламент", "2.3", rule.format("более 5 рабочих дней"),
                      directorate="ИБ", stage="проект")
        unrelated = Chunk("other::3.1", "other", "Другой документ", "3.1", "Карточка сохраняется в CRM.",
                          directorate="ИБ", stage="проект")
        decision = {"scope": "company_process", "status": "conflict", "text": "Источники 1 и 3: 5 и 3 дня.",
                    "source_ids": [1, 3]}
        response = {"message": {"content": json.dumps(decision)}}
        for threshold in ("более 3 рабочих дней", "не менее 5 рабочих дней"):
            with self.subTest(threshold=threshold):
                fixture = Chunk("fixture::2.3", "fixture", "Учебный пункт", "2.3", rule.format(threshold),
                                directorate="ИБ", stage="проект")
                chunks = [first, unrelated, fixture]
                search = SearchResult(hits=[Hit(chunk, 1.0) for chunk in chunks], scope=["ИБ"])
                with patch.dict(os.environ, {"AGENT_SOURCES": "documents"}), patch(
                    "app.agent.search_documents", return_value=(search, chunks)
                ), patch("app.agent.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
                    result = run_agent(question, [])

                self.assertEqual(result["status"], "conflict")
                self.assertEqual([source["chunk_id"] for source in result["citations"]],
                                 [first.chunk_id, fixture.chunk_id])
                self.assertIn("более 5 рабочих дней", result["answer"])
                self.assertIn(threshold, result["answer"])
                self.assertNotIn("Источники 1 и 3", result["answer"])
                self.assertTrue(result["answer"].endswith("[1] [2]"))

    def test_reply_deadlines_conflict_only_for_same_role_stage_and_status(self):
        first = {
            "chunk_id": "a", "source_type": "regulation", "directorate": "ИБ", "stage": "проект",
            "quote": "В случае отсутствия ответа от РП более 5 рабочих дней карточка переводится в статус «Закрыта».",
        }
        second = dict(first, chunk_id="b", quote="Если РП не отвечает более трёх рабочих дней, "
                      "карточка переводится в статус «Закрыта».")
        self.assertEqual(conflicting_reply_deadlines([first, second], [first]), [
            (first, "более 5 рабочих дней"), (second, "более трёх рабочих дней"),
        ])
        distinct = [
            dict(second, stage="пресейл"),
            dict(second, quote=second["quote"].replace("РП", "АМ")),
            dict(second, quote=second["quote"].replace("Закрыта", "Открыта")),
            dict(second, quote=second["quote"].replace("трёх", "пяти")),
        ]
        self.assertFalse(conflicting_reply_deadlines([first, *distinct], [first]))

        inclusive = dict(second, quote=second["quote"].replace("более трёх", "не менее пяти"))
        upper = dict(second, quote=second["quote"].replace("более трёх", "не более трёх"))
        self.assertFalse(conflicting_reply_deadlines([first, inclusive], [first]))
        self.assertFalse(conflicting_reply_deadlines([first, upper], [first], include_inclusive=True))


if __name__ == "__main__":
    unittest.main()
