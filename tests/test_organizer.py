import pandas as pd

import organizer


def test_run_organize_reports_two_steps(tmp_path, monkeypatch):
    csv_path = tmp_path / "drive.csv"
    pd.DataFrame([
        {
            "path": r"D:\Users\Stephen\doc.txt",
            "filename": "doc.txt",
            "summary": "A personal document about taxes.",
            "decision": "KEEP",
            "is_dir": "False",
        }
    ]).to_csv(csv_path, index=False)

    calls = []

    def fake_generate(base_url, model, prompt, temperature=0.0):
        calls.append({"prompt": prompt, "temperature": temperature})
        if len(calls) == 1:
            return "Finance\nPersonal documents"
        return '[{"original_path": "D:\\\\Users\\\\Stephen\\\\doc.txt", "organized_path": "Finance/doc.txt"}]'

    class FakeTqdm:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self.updates = []
            self.postfixes = []
            self.closed = False
            captured.append(self)

        def update(self, amount):
            self.updates.append(amount)

        def set_postfix_str(self, value):
            self.postfixes.append(value)

        def close(self):
            self.closed = True

    captured = []

    monkeypatch.setattr(organizer.llm_client, "check_ollama", lambda *args, **kwargs: None)
    monkeypatch.setattr(organizer.llm_client, "generate", fake_generate)
    monkeypatch.setattr(organizer, "tqdm", lambda *args, **kwargs: FakeTqdm(*args, **kwargs))

    organizer.run_organize(
        str(csv_path),
        {"ollama_base_url": "http://localhost:11434", "triage_model": "test-model"},
    )

    progress = captured[0]
    assert progress.kwargs["total"] == 2
    assert progress.postfixes == ["taxonomy", "assignments"]
    assert progress.updates == [1, 1]
    assert progress.closed is True

    df = pd.read_csv(csv_path, dtype=str)
    assert df.loc[0, "organized_path"] == "Finance/doc.txt"