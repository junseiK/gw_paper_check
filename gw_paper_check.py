import os
import requests
from datetime import datetime, timedelta, timezone
import arxiv
import google.generativeai as genai
import re
import time

def send_discord_notify(message):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        print("【エラー】DISCORD_WEBHOOK_URL が設定されていません。")
        return

    chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]
    for chunk in chunks:
        data = {"content": chunk}
        try:
            requests.post(webhook_url, json=data, timeout=10)
        except Exception as e:
            print(f"Discord通知失敗: {e}")
        time.sleep(1.5)

def clean_text(text):
    if not text: return ""
    return re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFDCF\uFDE0-\uFFFD]', '', text)

def fetch_and_summarize_gw_papers():
    print("=" * 50)
    print("重力波論文デイリーチェック（超軽量・最速版）を開始します...")
    print("=" * 50)

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    yesterday = now - timedelta(days=1)
    base_date = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    
    # 2. arXivから取得
    client = arxiv.Client(page_size=30, delay_seconds=5, num_retries=3)
    
    # ★変更点：限界まで削ぎ落とした超シンプルなクエリ
    # "gravitational wave" (単数形) のみで検索し、カテゴリー絞り込みも外す
    search_query = 'all:"gravitational wave"'
    
    search = arxiv.Search(
        query=search_query, 
        max_results=30,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )

    print(f"arXivから論文を取得中（クエリ: {search_query}）...")
    
    results = []
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            results = list(client.results(search))
            break
        except Exception as e:
            if "429" in str(e) and attempt < max_attempts:
                print(f"【警告】429エラー。60秒待機して再試行します...")
                time.sleep(60)
            else:
                error_msg = f"【お知らせ】arXivへのアクセスに失敗しました（サーバー混雑等）。\n詳細: `{e}`"
                print(error_msg)
                send_discord_notify(error_msg)
                return

    unique_papers = {p.entry_id: p for p in results if p.published >= base_date}
    filtered_papers = sorted(unique_papers.values(), key=lambda x: x.published, reverse=True)

    if not filtered_papers:
        send_discord_notify("本日は新着の重力波論文はありませんでした。")
        return

    # 3. Gemini設定
    GOOGLE_API_KEY = os.environ.get('GEMINI_API_KEY')
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-3.5-flash')

    print(f"全 {len(filtered_papers)} 件を 5 件ずつチャンク処理します...")
    final_output = f"**【本日の新着論文: {len(filtered_papers)}件】**\n\n"
    
    chunk_size = 5
    for i in range(0, len(filtered_papers), chunk_size):
        chunk = filtered_papers[i : i + chunk_size]
        chunk_text = ""
        for idx, paper in enumerate(chunk, i + 1):
            safe_title = clean_text(paper.title)
            safe_summary = clean_text(paper.summary)
            chunk_text += f"\n--- 論文番号: {idx} ---\n【タイトル】{safe_title}\n[アブスト]\n{safe_summary}\n"

        prompt = f"""
        あなたは重力波データ解析の専門家です。以下の論文リストを読み、各論文について3点を出力してください。
        数式等で難しければ「要約不可」でスキップ可能です。
        1. 【和訳要約】3行程度
        2. 【関連度スコア】データ解析との関連度（1〜10点）と理由
        3. 【判定】7点以上なら「★ピックアップ」、それ以外は「スルー」
        [リスト]
        {chunk_text}
        """

        print(f"--- グループ {i//chunk_size + 1} を解析中... ---")
        try:
            response = model.generate_content(prompt)
            final_output += response.text + "\n"
        except Exception as e:
            final_output += f"\n【エラー】論文番号 {i+1}〜 の解析に失敗しました: {e}\n"

        if i + chunk_size < len(filtered_papers):
            time.sleep(15)

    print("解析完了。Discordに通知を送信します。")
    send_discord_notify(final_output)

if __name__ == "__main__":
    fetch_and_summarize_gw_papers()
