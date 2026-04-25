import os
import time

import pandas as pd


def safe_write_csv(
    df: pd.DataFrame,
    path: str,
    retries: int = 3,
    retry_delay_seconds: float = 0.5,
) -> None:
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False)

    for attempt in range(retries + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            if attempt < retries:
                time.sleep(retry_delay_seconds)
                continue

            pending_path = path + ".pending"
            df.to_csv(pending_path, index=False)
            raise PermissionError(
                f"Could not replace '{path}' (it may be open in Excel or another program). "
                f"Close it and rerun. Latest data was saved to '{pending_path}'."
            ) from e
