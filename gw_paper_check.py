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

    # Discordの文字数制限(2000文字)対策は残しておきます
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
    print("重力波論文デイリーチェック（一括解析・研究特化版）を開始します...")
    print("=" * 50)

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    yesterday = now - timedelta(days=1)
    base_date = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    
    # 2. arXivから取得
    client = arxiv.Client(page_size=30, delay_seconds=5, num_retries=3)
    
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

    print(f"全 {len(filtered_papers)} 件を一括でAIに解析させます...")
    final_output = f"**【本日の新着論文: {len(filtered_papers)}件】**\n\n"
    
    # 5件ずつのループを廃止し、すべての論文を1つのテキストにまとめる
    all_papers_text = ""
    for idx, paper in enumerate(filtered_papers, 1):
        safe_title = clean_text(paper.title)
        safe_summary = clean_text(paper.summary)
        # ★ここに paper.entry_id (URL) を追加し、AIがリンクを出力できるようにしました
        all_papers_text += f"\n--- 論文番号: {idx} ---\n【タイトル】{safe_title}\n【URL】{paper.entry_id}\n[アブスト]\n{safe_summary}\n"

    prompt = f"""
    あなたは重力波データ解析の専門家です。
    以下のようなテーマに興味を持っています。特に1番上は研究のテーマであり、特別な興味を持っています。
    ・確率的重力波背景放射の非ガウス的な解析を機械学習を用いて行う
    ・上記に関する、他の確率論的重力波背景放射のこと、機械学習のこと
    ・ブラックホールの質量分布、階層進化の話
    ・ハッブルテンションを重力波から解く
    
    以下の論文リストを読み、各論文について5点を出力してください。
    1. 論文名
    2. arxivリンク
    3. 和訳要約(3行程度)
    4. あなたの興味との関連度（1〜10点）と理由
    5. 判定(7点以上なら「★ピックアップ」、それ以外は「スルー」)
    
    出力に関して、冒頭に「わかりました！」等書くことは不要です。レイアウトに沿って、わかりやすく、解析をお願いします。
    
    [リスト]
    {all_papers_text}
    """

    print("--- AIによる一括解析を実行中... ---")
    try:
        # 一発勝負で全件を投げる
        response = model.generate_content(prompt)
        final_output += response.text + "\n"
    except Exception as e:
        final_output += f"\n【エラー】AIの解析に失敗しました: {e}\n"

    print("解析完了。Discordに通知を送信します。")
    send_discord_notify(final_output)

if __name__ == "__main__":
    fetch_and_summarize_gw_papers()