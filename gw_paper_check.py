import os
import requests
from datetime import datetime, timedelta, timezone
import arxiv
import google.generativeai as genai
import re
import time

def send_discord_notify(message):
    """DiscordのWebhookにメッセージを送信する関数"""
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    
    if not webhook_url:
        print("【エラー】DISCORD_WEBHOOK_URL が設定されていません。")
        return

    # Discordは1メッセージ2000文字の制限があるため、安全に1900文字ずつ分割して送信
    chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]
    
    for chunk in chunks:
        data = {"content": chunk}
        response = requests.post(webhook_url, json=data)
        if response.status_code not in [200, 204]:
            print(f"Discord通知失敗。ステータスコード: {response.status_code}")
        time.sleep(1) # 連続送信時のエラー回避

def fetch_and_summarize_gw_papers():
    print("=" * 50)
    print("重力波論文デイリーチェックを開始します...")
    print("=" * 50)

    # 日本時間の昨日0時を基準にする
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    yesterday = now - timedelta(days=1)
    base_date = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    
    client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
    categories = '(cat:gr-qc OR cat:astro-ph.HE OR cat:astro-ph.IM OR cat:astro-ph.CO)'

    def fetch_papers(query):
        search = arxiv.Search(
            query=query, max_results=60,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending
        )
        return list(client.results(search))

    results_singular = fetch_papers(f'all:"gravitational wave" AND {categories}')
    results_plural = fetch_papers(f'all:"gravitational waves" AND {categories}')

    unique_papers = {p.entry_id: p for p in (results_singular + results_plural) if p.published >= base_date}
    filtered_papers = sorted(unique_papers.values(), key=lambda x: x.published, reverse=True)

    if not filtered_papers:
        send_discord_notify("本日は新着の重力波論文はありませんでした。")
        return

    # 環境変数からAPIキーを取得
    GOOGLE_API_KEY = os.environ.get('GEMINI_API_KEY')
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-3.5-flash')

    def clean_text(text):
        if not text: return ""
        return re.sub(r'[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFDCF\uFDE0-\uFFFD]', '', text)

    all_abstracts_text = ""
    for idx, paper in enumerate(filtered_papers, 1):
        safe_title = clean_text(paper.title)
        safe_summary = clean_text(paper.summary)
        all_abstracts_text += f"\n--- 論文番号: {idx} ---\n【タイトル】{safe_title}\n[アブスト]\n{safe_summary}\n"

    prompt = f"""
    あなたは重力波データ解析の専門家です。
    以下のようなテーマに興味を持っています。特に一番上は研究のテーマであり、特別な興味を持っています。確率的重力波背景放射であれば他のテーマでも知っておきたいと思っています。
    ・確率的重力波背景放射の非ガウス型(popcorn型)の考察を機械学習を用いて行う
    ・ブラックホールの質量分布、階層的進化の話
    ・ハッブルテンションを重力波から紐解く

    以下の論文リスト（全{len(filtered_papers)}件）をすべて読み、各論文について3点を出力してください。
    1. 【和訳要約】3行程度
    2. 【関連度スコア】あなたの興味との関連度（1〜10点）と理由
    3. 【判定】6点以上なら「★ピックアップ」、それ以外は「スルー」
    [リスト]
    {all_abstracts_text}
    """

    try:
        response = model.generate_content(prompt)
        # Markdownで太字装飾して目立たせる
        header_msg = f"**【本日の新着論文: {len(filtered_papers)}件】**\n"
        final_message = header_msg + response.text
        
        # Discordへ送信
        send_discord_notify(final_message)
        print("Discordへの通知が完了しました！")
        
    except Exception as e:
        error_msg = f"AIの解析中にエラーが発生しました: {e}"
        print(error_msg)
        send_discord_notify(error_msg)

if __name__ == "__main__":
    fetch_and_summarize_gw_papers()