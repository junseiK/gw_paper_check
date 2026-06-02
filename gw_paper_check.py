import os
import random
import requests
from datetime import datetime, timedelta, timezone
import arxiv
from google import genai
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
        data = {
            "content": chunk,
            "flags": 4
            # flags: URLの埋め込み表示を無効化
        }
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
    client = arxiv.Client(
        page_size=30,
        delay_seconds=10+random.random() * 5,  # ランダムな遅延を追加してサーバーへの負荷を分散
        num_retries=3
        )
    
    search_query = 'all:"gravitational wave"'
    
    search = arxiv.Search(
        query=search_query, 
        max_results=30,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )

    print(f"arXivから論文を取得中（クエリ: {search_query}）...")

    wait_times = [
        60+random.random() * 5,
        180+random.random() * 5,
        300+random.random() * 5
    ]
    
    results = []
    errmsg=""
    for attempt, wait in enumerate(wait_times + [0]):
        try:
            # ここで通信を発生させる
            results = list(client.results(search))
            print("無事にarXivから論文を取得成功しました！")
            break  # 成功したらループを抜ける！

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "503" in error_msg:
                if wait > 0:
                    print(f"【警告】サーバー混雑を検知。{wait}秒待機して再試行します... (試行 {attempt + 1}/{len(wait_times)})")
                    time.sleep(wait)
                else:
                    errmsg = f"【完全敗北】再試行の上限に達しました。詳細: {error_msg}" 
                    print(errmsg)
                    break
            else:
                errmsg = f"想定外のエラーが発生しました: {error_msg}"
                print(errmsg)
                break
    if not errmsg=="":
        send_discord_notify(errmsg)
        return

    unique_papers = {p.entry_id: p for p in results if p.published >= base_date}
    filtered_papers = sorted(unique_papers.values(), key=lambda x: x.published, reverse=True)

    if not filtered_papers:
        date_str = now.strftime("%Y年%m月%d日")
        send_discord_notify(f"【{date_str}】本日は新着の重力波論文はありませんでした。")
        return

    # 3. Gemini設定（★ここを新SDKの書き方に完全修正しました）
    GOOGLE_API_KEY = os.environ.get('GEMINI_API_KEY')
    ai_client = genai.Client(api_key=GOOGLE_API_KEY)

    print(f"全 {len(filtered_papers)} 件を一括でAIに解析させます...")
    
    date_str = now.strftime("%Y年%m月%d日")
    final_output = f"**【本日({date_str})の新着論文: {len(filtered_papers)}件】**\n\n"
    
    # 5件ずつのループを廃止し、すべての論文を1つのテキストにまとめる
    all_papers_text = ""
    for idx, paper in enumerate(filtered_papers, 1):
        safe_title = clean_text(paper.title)
        safe_summary = clean_text(paper.summary)
        # ★ここに paper.entry_id (URL) を追加し、AIがリンクを出力できるようにしました
        all_papers_text += f"\n--- 論文番号: {idx} ---\n【タイトル】{safe_title}\n【URL】{paper.entry_id}\n[アブスト]\n{safe_summary}\n"

    prompt = f"""
    私は重力波データ解析を研究している大学院生です。
    以下のようなテーマに興味を持っています。特に1番上は研究のテーマであり、特別な興味を持っています。
    ・確率的重力波背景放射の非ガウス的な解析を機械学習を用いて行う
    ・上記に関する、他の確率論的重力波背景放射のこと、機械学習のこと
    ・ブラックホールの質量分布、階層進化の話
    ・ハッブルテンションを重力波から解く
    
    あなたは重力波やそれに関する物理学の専門家として、以下の論文リストを読み、各論文について5つを出力してください。
    1. 論文名とその和訳
    2. arxivリンク（この文はdiscordに転送します。押せばリンクに飛ぶようにしたいので、むき出しのURLだけをシンプルに貼ってください。）
    3. 和訳要約(3行程度)
    4. あなたの興味との関連度（1〜10点、太字で強調）、判定(7点以上なら「★ピックアップ」、それ以外は「スルー」)
    5. 点数の理由

    出力に関して、冒頭に「わかりました！」等書くことは不要です。レイアウトに沿って、わかりやすく、解説をお願いします。
    また、論文と論文の間の仕切りや点数は最大限強調して、その他も長文であることを考慮して見やすくレイアウトしてください。
    
    [リスト]
    {all_papers_text}
    """

    print("--- AIによる一括解析を実行中... ---")
    gemini_wait_times = [
        30+random.random() * 5,
        60+random.random() * 5,
        120+random.random() * 5
    ]
    
    for attempt, wait in enumerate(gemini_wait_times + [0]):
        try:
            # 一発勝負で全件を投げる
            response = ai_client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
            )
            final_output += response.text + "\n"
            print("AIによる解析が無事に完了しました！")
            break  # 成功したらループを抜ける！

        except Exception as e:
            error_msg = str(e)
            # 503(混雑) または 429(制限) の場合は待機してリトライ
            if "503" in error_msg or "429" in error_msg:
                if wait > 0:
                    print(f"【AI混雑】Geminiサーバーが混雑中。{wait}秒待機して再試行します... (試行 {attempt + 1}/{len(gemini_wait_times)})")
                    time.sleep(wait)
                else:
                    print(f"【完全敗北】Geminiの再試行上限に達しました。")
                    final_output += f"\n【エラー】AIのサーバー混雑が解消されませんでした: {error_msg}\n"
                    break
            else:
                # それ以外の致命的なエラー（APIキー間違いなど）は即終了
                print(f"【エラー】想定外のAI解析エラー: {error_msg}")
                final_output += f"\n【エラー】想定外のAI解析エラー: {error_msg}\n"
                break
                
    print("解析完了。Discordに通知を送信します。")
    send_discord_notify(final_output)

if __name__ == "__main__":
    fetch_and_summarize_gw_papers()
