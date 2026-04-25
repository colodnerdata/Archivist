import pandas as pd
import pytest


@pytest.fixture
def sample_df():
    return pd.DataFrame([
        {"path": r"D:\Users\Stephen\Documents",                   "is_dir": "True",  "review": "",       "decision": "KEEP",   "summary": ""},
        {"path": r"D:\Users\Stephen\Documents\resume.docx",       "is_dir": "False", "review": "True",   "decision": "",       "summary": ""},
        {"path": r"D:\Users\Stephen\Documents\old\letter.pdf",    "is_dir": "False", "review": "",       "decision": "DELETE", "summary": ""},
        {"path": r"D:\Users\Stephen\Documents\old",               "is_dir": "True",  "review": "",       "decision": "",       "summary": ""},
        {"path": r"D:\Windows",                                    "is_dir": "True",  "review": "",       "decision": "DELETE", "summary": ""},
        {"path": r"D:\Windows\notepad.exe",                        "is_dir": "False", "review": "",       "decision": "",       "summary": ""},
        {"path": r"D:\Unreviewed\file.txt",                        "is_dir": "False", "review": "",       "decision": "",       "summary": ""},
    ])
