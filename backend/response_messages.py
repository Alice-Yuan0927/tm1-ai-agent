from typing import Any


def no_usable_data_message(skipped_sources: list[dict[str, Any]]) -> tuple[str, str]:
    cubes = [str(source.get("cube", "")).strip() for source in skipped_sources if source.get("cube")]
    no_data_count = sum(1 for source in skipped_sources if str(source.get("status", "")).lower() == "no data")
    error_count = len(skipped_sources) - no_data_count
    cube_text = ", ".join(cubes[:3])

    if skipped_sources and no_data_count == len(skipped_sources):
        message = "I couldn't find matching TM1 data for this request."
    else:
        message = "I couldn't return data for this request."
    if cube_text:
        message += f" I checked {cube_text}."
    if error_count:
        message += " Some candidate sources also failed validation or MDX execution."
    message += " Try confirming the scenario, period, entity, or whether you want full year/YTD."

    detail = "; ".join(
        f"{source.get('cube', '')} ({source.get('status', '')})"
        for source in skipped_sources
        if source.get("cube")
    )
    return message, detail
