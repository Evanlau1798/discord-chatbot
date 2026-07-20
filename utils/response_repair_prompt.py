from __future__ import annotations

from utils.imagine_config import is_image_generation_enabled


def build_repair_instruction() -> str:
    image_generation_enabled = is_image_generation_enabled()
    schema = '{"replyText":"..."'
    if image_generation_enabled:
        schema += ',"imageGeneration":{"needed":true,"operation":"create|edit","prompt":"...","sourceImageIds":["..."],"usePersonaIdentity":false}'
        schema += ',"imageReference":{"messageReferenceIds":["discord-message:..."]}'
    schema += ',"memory":{"update":true,"content":"..."},"browser":{"search":{"queries":["..."],"language":"zh-TW","region":"TW","sourceProfile":"mixed","desiredSources":3},"youtubeSearchQuery":"..."}}'
    parts = [
        "你上一輪沒有正確遵守輸出格式。請只回傳單一 JSON 物件，不要 Markdown、不要說明文字。"
        f"格式固定為 {schema}。",
        "replyText 內需要提供 URL 時，請使用 Discord Markdown 格式 [有意義的顯示文字](https://example.com)，"
        "連結 URL 必須是實際來源，不要放在反引號中。",
    ]
    if image_generation_enabled:
        parts.append("不需要生圖時省略 imageGeneration；")
    parts.append(
        ("不需要載入歷史圖片時省略 imageReference；" if image_generation_enabled else "")
        + "不需要更新記憶時省略 memory；不需要上網時省略 browser。"
        "如果目前請求或前一輪請求包含圖片，請加入 imageUnderstanding: "
        '{"summary":"...","visibleText":["..."],"details":["..."]}。'
    )
    if image_generation_enabled:
        parts.extend([
            "除非使用者明確指示在圖片中加入特定文字，否則 imageGeneration.prompt 不要加入明文文字。",
            "imageGeneration.operation 使用 create 時不得輸出 sourceImageIds；使用 edit 時必須從 "
            "payload.imageGenerationCandidates 選擇一個或多個 sourceImageIds。若找不到使用者指稱的原圖，"
            "請在 replyText 要求使用者重新附圖或直接回覆原圖，並省略 imageGeneration。",
            "edit 需要讓目前人設角色出現在結果中時，設定 usePersonaIdentity: true；"
            "人設身份必須優先於來源圖片中的人物特徵且不得混合。一般圖片修改則省略或設為 false。",
            "若需要尚未載入的歷史圖片，只能從 payload.historicalImageReferences 選擇 messageReferenceIds，"
            "單獨輸出 imageReference 並省略 replyText、imageGeneration 與 browser；收到圖片候選後不可再次請求。",
        ])
    parts.append(
        "需要網頁搜尋或最新資料時，不要先輸出 replyText，直接輸出 browser.search.queries 的精簡查詢關鍵字；"
        "可選擇提供 language、region、timeRange、siteDomains、sourceProfile 與 3 到 5 的 desiredSources；"
        "需要搜尋 YouTube 影片、yt 影片、shorts 或剪輯連結時，優先輸出 browser.youtubeSearchQuery；"
        "收到 browserResults 後才輸出具有人設語氣的 replyText。"
        "若前一輪需要搜尋海外人物、遊戲、實況主、影片、梗圖或片段，請使用英文別名、常見英文說法與最多三個查詢關鍵字，"
        "不要只輸出使用者原文；第一個 query 必須是可單獨執行的最精準主查詢。"
        "若使用者指定 YouTube 或影片，請使用 browser.youtubeSearchQuery 並加入可能的英文標題線索。"
        "除非使用者明確提供 URL，否則上網請優先使用 browser.searchQuery；"
        "需要在指定網頁中尋找文字時可用 browser.find: {\"url\":\"...\",\"pattern\":\"...\"}。"
        "需要查看指定網頁內圖片時，可在 browser 中加入 includeImages: true。"
    )
    return "".join(parts)
