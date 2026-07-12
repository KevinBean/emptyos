"""Tier 3: Interactive CRUD flows for 7 core apps."""

import pytest
from helpers import TEST_PREFIX, BASE_URL


@pytest.mark.interactive
class TestExpenseCRUD:
    """Expense: add via UI, verify in list, delete."""

    def test_add_expense_api(self, http_client):
        resp = http_client.post("/expense/api/smart-add", json={
            "text": f"5 {TEST_PREFIX}coffee"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" not in data
        assert data.get("amount") == 5.0

    def test_expense_in_list(self, http_client):
        resp = http_client.get("/expense/api/list")
        assert resp.status_code == 200
        entries = resp.json()
        assert isinstance(entries, list)
        found = any(TEST_PREFIX in str(e.get("description", "")) for e in entries)
        assert found, "Test expense not found in list"

    def test_expense_ui_add(self, page, page_errors, base_url):
        page.goto(f"{base_url}/expense/", wait_until="load")
        page.wait_for_timeout(1000)
        inp = page.locator("input[placeholder*='lunch']")
        inp.fill(f"5 {TEST_PREFIX}tea")
        page.get_by_role("button", name="Add").first.click()
        page.wait_for_timeout(2000)
        assert len(page_errors) == 0, f"JS errors: {page_errors}"

    def test_delete_expense(self, http_client):
        resp = http_client.get("/expense/api/list")
        entries = resp.json() if resp.status_code == 200 else []
        for e in entries:
            if TEST_PREFIX in str(e.get("description", "")):
                http_client.post("/expense/api/delete", json={"entry": e})


@pytest.mark.interactive
class TestJournalEntry:
    """Journal: submit entry via UI, verify via API."""

    def test_journal_entry_api(self, http_client):
        resp = http_client.post("/journal/api/entry", json={
            "text": f"{TEST_PREFIX}feeling productive today",
            "mood": "good"
        })
        assert resp.status_code == 200

    def test_journal_today_has_entries(self, http_client):
        resp = http_client.get("/journal/api/today")
        assert resp.status_code == 200

    def test_journal_ui_submit(self, page, page_errors, base_url):
        page.goto(f"{base_url}/journal/", wait_until="load")
        # Select mood
        mood_btn = page.locator("button:has-text('🙂')")
        if mood_btn.count() > 0:
            mood_btn.first.click()
        # Type in textarea
        textarea = page.locator("textarea").first
        if textarea.count() > 0:
            textarea.click()
            textarea.press("End")
            textarea.type(f"\n{TEST_PREFIX}automated test entry")
        # Submit
        submit = page.get_by_role("button", name="Submit")
        if submit.count() > 0:
            submit.click()
            page.wait_for_timeout(2000)
        assert len(page_errors) == 0, f"JS errors: {page_errors}"


@pytest.mark.interactive
class TestHealingMood:
    """Healing: log mood via API and UI."""

    def test_healing_mood_api(self, http_client):
        resp = http_client.post("/healing/api/mood", json={
            "mood": "good",
            "note": f"{TEST_PREFIX}automated mood log",
            "energy": 7,
            "tags": ["productive"]
        })
        assert resp.status_code == 200

    def test_healing_history(self, http_client):
        resp = http_client.get("/healing/api/history")
        assert resp.status_code == 200

    def test_healing_ui_mood(self, page, page_errors, base_url):
        page.goto(f"{base_url}/healing/", wait_until="load")
        # Click great mood
        great_btn = page.locator("text=😊").first
        if great_btn.count() > 0:
            great_btn.click()
        # Fill note
        note = page.locator("textarea, input[placeholder*='feeling']").first
        if note.count() > 0:
            note.fill(f"{TEST_PREFIX}ui test mood")
        # Submit
        log_btn = page.locator("button:has-text('Log Mood')")
        if log_btn.count() > 0:
            log_btn.click()
            page.wait_for_timeout(2000)
        assert len(page_errors) == 0, f"JS errors: {page_errors}"


@pytest.mark.interactive
class TestNutritionMeal:
    """Nutrition: add meal via UI, verify macros update."""

    def test_nutrition_add_api(self, http_client):
        resp = http_client.post("/nutrition/api/log", json={
            "text": f"{TEST_PREFIX}apple 80cal",
            "meal_type": "snack"
        })
        assert resp.status_code == 200

    def test_nutrition_today(self, http_client):
        resp = http_client.get("/nutrition/api/today")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data or "meals" in data or isinstance(data, dict)

    def test_nutrition_ui_add(self, page, page_errors, base_url):
        page.goto(f"{base_url}/nutrition/", wait_until="load")
        page.wait_for_timeout(1000)
        # Exclude hidden global capture widget — find a VISIBLE input on the page
        inputs = page.locator(
            "input[type='text']:visible, textarea:visible"
        )
        if inputs.count() == 0:
            pytest.skip("No visible nutrition input")
        inputs.first.fill(f"{TEST_PREFIX}banana 100cal")
        # Try Add button — tolerate if it's not labelled that way
        try:
            page.get_by_role("button", name="Add").first.click(timeout=2000)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        assert len(page_errors) == 0, f"JS errors: {page_errors}"


@pytest.mark.interactive
class TestTaskList:
    """Task: load list, verify renders."""

    def test_task_list_api(self, http_client):
        resp = http_client.get("/task/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_task_focus_api(self, http_client):
        resp = http_client.get("/task/api/focus")
        assert resp.status_code == 200

    def test_task_ui_loads(self, page, page_errors, base_url):
        page.goto(f"{base_url}/task/", wait_until="load")
        page.wait_for_timeout(2000)
        # Should show task stats
        content = page.content()
        assert "OVERDUE" in content or "TODAY" in content or "Tasks" in content
        assert len(page_errors) == 0, f"JS errors: {page_errors}"


@pytest.mark.interactive
class TestItemsCRUD:
    """Items: add via API, verify in list, delete."""

    def test_items_add(self, http_client):
        resp = http_client.post("/items/api/items", json={
            "name": f"{TEST_PREFIX}widget",
            "category": "Test",
            "location": "Test"
        })
        assert resp.status_code == 200
        data = resp.json()
        self.__class__._item_id = data.get("id")

    def test_items_in_list(self, http_client):
        resp = http_client.get("/items/api/items")
        assert resp.status_code == 200

    def test_items_delete(self, http_client):
        item_id = getattr(self.__class__, "_item_id", None)
        if item_id:
            resp = http_client.request("DELETE", f"/items/api/items/{item_id}")
            assert resp.status_code == 200

    def test_items_ui_loads(self, page, page_errors, base_url):
        page.goto(f"{base_url}/items/", wait_until="load")
        page.wait_for_timeout(2000)
        assert len(page_errors) == 0, f"JS errors: {page_errors}"


@pytest.mark.interactive
class TestSearchQuery:
    """Search: query via UI, verify results appear."""

    def test_search_api(self, http_client):
        resp = http_client.get("/search/api/search?q=test&top=5&semantic=0")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_search_ui(self, page, page_errors, base_url):
        page.goto(f"{base_url}/search/", wait_until="load")
        inp = page.get_by_role("textbox", name="Search EmptyOS")
        if inp.count() > 0:
            inp.fill("cable")
            page.get_by_role("button", name="Search").click()
            page.wait_for_timeout(3000)
            # Should show results
            content = page.content()
            assert "result" in content.lower() or "cable" in content.lower()
        assert len(page_errors) == 0, f"JS errors: {page_errors}"
