#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""시험 일정 수집기. 토익(YBM), 토익스피킹(YBM), 국가기술자격 기사 정기시험(Q-net) 일정을 받아 data/exams.json에 쓴다.
표준 라이브러리만 쓴다. 인증키 없음. 출처 페이지가 바뀌어 한 곳이 실패하면 그 항목은 지난 결과를 그대로 두고 stale 표시만 한다.

실행: python3 collect_exams.py   (윈도우: python collect_exams.py)
"""
import json, os, re, sys, html, datetime as dt
from urllib import request as rq, parse as up

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "exams.json")
UA = "Mozilla/5.0 (gong-guide exam schedule collector; standard library)"
TIMEOUT = 25

SRC = {
    "toeic": "https://exam.toeic.co.kr/receipt/examSchList.php",
    "toeic_sw": "https://www.toeicswt.co.kr/receipt/examSchList.php",
    "qnet": "https://www.q-net.or.kr/crf021.do?id=crf02101s03&SERIES_CD=03",      # 기사·산업기사 연간 일정(페이지가 쓰는 조회 주소)
    "qnet_page": "https://www.q-net.or.kr/crf021.do?id=crf02101&gSite=Q&gId=&scheType=03",
}

# 안내서가 다루는 종목만 저장한다(환경 직렬 기사, 화공, 공기업 가점 자격). 전체 종목은 수백 개라 넣지 않는다.
INTEREST = [
    "수질환경기사", "대기환경기사", "폐기물처리기사", "소음진동기사", "토양환경기사", "온실가스관리기사",
    "자연생태복원기사", "환경위해관리기사", "화공기사", "화학분석기사", "산업안전기사", "산업위생관리기사",
    "가스기사", "위험물산업기사", "에너지관리기사", "신재생에너지발전설비기사(태양광)", "정보처리기사",
]

DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


def get(url, data=None):
    req = rq.Request(url, data=up.urlencode(data).encode() if data else None, headers={"User-Agent": UA})
    with rq.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def text(frag):
    """태그를 떼고 공백을 정리한 글자만."""
    frag = re.sub(r"<br\s*/?>|</br>", " ", frag, flags=re.I)
    frag = re.sub(r"<[^>]+>", " ", frag)
    return re.sub(r"\s+", " ", html.unescape(frag)).strip()


def rows(tbl_html):
    return re.findall(r"<tr[^>]*>(.*?)</tr>", tbl_html, flags=re.S | re.I)


def cells(row_html):
    return [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S | re.I)]


def dates(s):
    return ["%s-%s-%s" % m for m in DATE.findall(s)]


def tbody(page, nth=0):
    bodies = re.findall(r"<tbody[^>]*>(.*?)</tbody>", page, flags=re.S | re.I)
    return bodies[nth] if len(bodies) > nth else ""


# ---------- 토익 ----------
def collect_toeic():
    page = get(SRC["toeic"])
    out = []
    for r in rows(tbody(page)):
        c = cells(r)
        if len(c) < 4:
            continue
        m = re.search(r"제\s*(\d+)\s*회", c[0])
        d = dates(c[1]); res = dates(c[2]); reg = dates(c[3])
        if not (m and d):
            continue
        item = {"round": int(m.group(1)), "date": d[0], "result": res[0] if res else None,
                "reg_beg": reg[0] if len(reg) > 0 else None, "reg_end": reg[1] if len(reg) > 1 else None}
        if len(reg) >= 4:
            item["extra_beg"], item["extra_end"] = reg[2], reg[3]
        t = re.search(r"(\d{1,2}:\d{2})", c[1]); item["time"] = t.group(1) if t else None
        out.append(item)
    if not out:
        raise RuntimeError("토익 표를 못 읽음")
    return out


# ---------- 토익스피킹 ----------
def collect_toeic_sw():
    page = get(SRC["toeic_sw"])
    out = []
    for r in re.findall(r"<tr([^>]*)>(.*?)</tr>", tbody(page), flags=re.S | re.I):
        attrs, body = r
        if "speaking" not in attrs:      # 같은 표에 Writing, S&W 행이 섞여 있으면 거른다
            continue
        c = cells(body)
        if len(c) < 3:
            continue
        d = dates(c[0]); res = dates(c[1]); reg = dates(c[2])
        if not d:
            continue
        out.append({"date": d[0], "result": res[0] if res else None,
                    "reg_beg": reg[0] if len(reg) > 0 else None, "reg_end": reg[1] if len(reg) > 1 else None})
    if not out:
        raise RuntimeError("토익스피킹 표를 못 읽음")
    return out


# ---------- Q-net 기사 정기시험 ----------
def collect_qnet_year(yy):
    page = get(SRC["qnet"] + "&IMPL_YY=%d" % yy, data={"SERIES_CD": "03", "IMPL_YY": str(yy)})
    sched = []
    for r in rows(tbody(page, 0)):
        c = cells(r)
        if len(c) < 8:
            continue
        m = re.search(r"제\s*(\d+)\s*회", c[0])
        if not m or "기사" not in c[0]:
            continue
        dreg = dates(c[1]); dexam = dates(c[2]); dpass = dates(c[3]); dcert = dates(c[4])
        preg = dates(c[5]); pexam = dates(c[6]); final = dates(c[7])
        sched.append({
            "seq": int(m.group(1)),
            "doc_reg_beg": dreg[0] if dreg else None, "doc_reg_end": dreg[1] if len(dreg) > 1 else None,
            "doc_reg_extra_beg": dreg[2] if len(dreg) > 2 else None, "doc_reg_extra_end": dreg[3] if len(dreg) > 3 else None,
            "doc_exam_beg": dexam[0] if dexam else None, "doc_exam_end": dexam[-1] if dexam else None,
            "doc_pass": dpass[0] if dpass else None,
            "doc_cert_beg": dcert[0] if dcert else None, "doc_cert_end": dcert[-1] if dcert else None,
            "prac_reg_beg": preg[0] if preg else None, "prac_reg_end": preg[1] if len(preg) > 1 else None,
            "prac_reg_extra_beg": preg[-2] if len(preg) >= 4 else None, "prac_reg_extra_end": preg[-1] if len(preg) >= 4 else None,
            "prac_exam_beg": pexam[0] if pexam else None, "prac_exam_end": pexam[-1] if pexam else None,
            "final": final,                      # 종목군에 따라 발표일이 둘일 수 있다
        })
    # 회별 시행 종목표: 회차 칸(rowspan) 뒤에 종목 묶음 행이 여러 개 온다. 각 묶음의 마지막 칸이 그 묶음의 최종 발표일.
    subjects = {}
    seq = None
    for r in rows(tbody(page, 1)):
        head = re.search(r"<td[^>]*rowspan[^>]*>(.*?)</td>", r, flags=re.S | re.I)
        if head:
            m = re.search(r"제\s*(\d+)\s*회", text(head.group(1)))
            seq = int(m.group(1)) if m else seq
        ul = re.search(r"<ul[^>]*>(.*?)</ul>", r, flags=re.S | re.I)
        if not (ul and seq):
            continue
        tail = re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S | re.I)
        fin = dates(text(tail[-1])) if tail else []
        for li in re.findall(r"<li[^>]*>(.*?)</li>", ul.group(1), flags=re.S | re.I):
            t = text(li)
            names = [n.strip() for n in t.split(":", 1)[-1].split(",")]
            for n in names:
                if n in INTEREST:
                    subjects.setdefault(n, []).append({"seq": seq, "final": fin[0] if fin else None})
    if not sched:
        raise RuntimeError("Q-net %d년 기사 일정 표를 못 읽음" % yy)
    if not any((r["doc_reg_beg"] or r["doc_exam_beg"] or "").startswith(str(yy)) for r in sched):
        raise RuntimeError("Q-net이 %d년 대신 다른 해 표를 돌려줌(아직 미게시)" % yy)   # IMPL_YY에 없는 해를 넣으면 올해 표가 온다
    return {"rounds": sched, "subjects": subjects}


def collect_qnet():
    this = dt.date.today().year
    years = {}
    for yy in (this, this + 1):
        try:
            years[str(yy)] = collect_qnet_year(yy)
        except Exception as e:                   # 다음 해 일정은 보통 12월에 올라온다. 없으면 조용히 넘어간다
            if yy == this:
                raise
            print("qnet %d: 아직 없음 (%s)" % (yy, e))
    return years


def main():
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
    out = {"collected_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "interest": INTEREST,
           "sources": {"toeic": SRC["toeic"], "toeic_sw": SRC["toeic_sw"], "qnet": SRC["qnet_page"]}, "stale": []}
    for key, fn in (("toeic", collect_toeic), ("toeic_sw", collect_toeic_sw), ("qnet", collect_qnet)):
        try:
            out[key] = fn()
            print("%s: OK (%d)" % (key, len(out[key])))
        except Exception as e:
            print("%s: 실패 (%s) → 지난 결과 유지" % (key, e), file=sys.stderr)
            out[key] = prev.get(key)
            out["stale"].append(key)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", OUT)
    return 1 if len(out["stale"]) == 3 else 0


if __name__ == "__main__":
    sys.exit(main())
