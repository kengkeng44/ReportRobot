"""信件 parser。每個發信人一個模組，統一回傳交易 dict 清單。

交易 dict 的欄位對應 Notion「交易明細」DB：
    date, time, amount, shop, category, direction, status,
    source, card_last4, region, fingerprint

共同原則：**解析不出來就跳過那一筆，不要寫半殘資料進 Notion。**
一筆金額錯誤的紀錄比缺一筆更難發現，也更難修。
"""
