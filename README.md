# GoingStock Streamlit

本项目是本地投资信息采集与可视化工具。

## 功能

- 盘前新闻事件包
- 隔夜外部变量
- 雪球盘中行情包
- 雪球快讯采集
- 大盘资金流（东方财富 Cookie 直连）
- 行业/概念资金流横向表（东方财富 Cookie 直连）

## 本地运行

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m streamlit run app.py --server.port 8503 --server.address localhost
```

## 必须手动创建但不能上传的文件

```text
xueqiu_cookie.txt
eastmoney_cookie.txt
```

参考：

```text
xueqiu_cookie.example.txt
eastmoney_cookie.example.txt
```

## 注意

outputs 和真实 Cookie 已被 .gitignore 排除。
