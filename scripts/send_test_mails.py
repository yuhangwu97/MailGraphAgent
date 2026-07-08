"""
发送测试邮件到 yuhangwu1022@gmail.com
生成带附件的仿真商务邮件，用于测试 IMAP → 解析 → AI 提取 → 图谱 全链路
"""
import smtplib
import os
import io
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
USER = "yuhangwu1022@gmail.com"
PASS = "qnaemdaysmkcedma"
TO = "yuhangwu1022@gmail.com"

# ═══════════════════════════════════════
# 创建模拟附件
# ═══════════════════════════════════════

def create_pdf_attachment() -> bytes:
    """生成一个模拟 PDF 文件（项目进度报告）"""
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer<</Size 4/Root 1 0 R>>
startxref
206
%%EOF"""
    return content


def create_excel_attachment() -> bytes:
    """生成一个模拟 Excel 文件（报价单）"""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>''')
        zf.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        zf.writestr('xl/workbook.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="报价单" sheetId="1" r:id="rId1"/></sheets>
</workbook>''')
        zf.writestr('xl/_rels/workbook.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>''')
        zf.writestr('xl/worksheets/sheet1.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>
    <row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2"><v>100</v></c><c r="C2" t="s"><v>5</v></c><c r="D2"><v>3500000</v></c></row>
    <row r="3"><c r="A3" t="s"><v>6</v></c><c r="B3"><v>50</v></c><c r="C3" t="s"><v>7</v></c><c r="D3"><v>1200000</v></c></row>
    <row r="4"><c r="A4" t="s"><v>8</v></c><c r="B4"><v>1</v></c><c r="C4" t="s"><v>9</v></c><c r="D4"><v>800000</v></c></row>
    <row r="5"><c r="A5" t="s"><v>10</v></c><c r="B5"><v>3</v></c><c r="C5" t="s"><v>11</v></c><c r="D5"><v>450000</v></c></row>
  </sheetData>
</worksheet>''')
        zf.writestr('xl/sharedStrings.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="12" uniqueCount="12">
  <si><t>项目名称</t></si><si><t>数量</t></si><si><t>单位</t></si><si><t>金额(元)</t></si>
  <si><t>智能仓储管理系统 WMS</t></si><si><t>套</t></si>
  <si><t>移动终端适配模块</t></si><si><t>台</t></si>
  <si><t>系统集成与调试</t></si><si><t>次</t></si>
  <si><t>培训与技术支持</t></si><si><t>人/天</t></si>
</sst>''')
    return buf.getvalue()


def create_docx_attachment() -> bytes:
    """生成一个模拟 Word 文件（会议纪要）"""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>''')
        zf.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>''')
        zf.writestr('word/document.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>X智能仓储系统项目周会纪要</w:t></w:r></w:p>
    <w:p><w:r><w:t>会议时间：2026年7月7日 14:00-15:30</w:t></w:r></w:p>
    <w:p><w:r><w:t>参会人员：张伟(A公司)、王芳(我方)、李经理(A公司技术)</w:t></w:r></w:p>
    <w:p><w:r><w:t>1. 项目进度：WMS核心模块已完成70%，移动端适配预计下周五完成。</w:t></w:r></w:p>
    <w:p><w:r><w:t>2. 风险点：第三方硬件接口文档未提供，可能影响联调进度。</w:t></w:r></w:p>
    <w:p><w:r><w:t>3. 下一步：安排UAT测试环境搭建，预计7月15日前完成。</w:t></w:r></w:p>
    <w:p><w:r><w:t>4. 合同金额确认：项目总额350万元，首付款30%已到账。</w:t></w:r></w:p>
  </w:body>
</w:document>''')
    return buf.getvalue()


def send_email(subject: str, body: str, attachment: tuple[str, bytes, str] | None = None):
    msg = MIMEMultipart()
    msg["From"] = USER
    msg["To"] = TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if attachment:
        fname, data, mime = attachment
        part = MIMEBase(*mime.split("/"))
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(USER, PASS)
        smtp.send_message(msg)
    print(f"   ✅ 已发送: {subject}")


# ═══════════════════════════════════════
# 发送测试邮件
# ═══════════════════════════════════════

print("=" * 55)
print("发送测试邮件到 yuhangwu1022@gmail.com")
print("=" * 55)

# 邮件 1：项目进度汇报 + PDF 附件
send_email(
    "关于X智能仓储系统升级项目的进度汇报",
    """王工，您好。

向您汇报一下X智能仓储系统升级项目的最新进展：

1. 本周完成了WMS核心模块的联调测试，系统运行稳定，预计7月20日可以进入UAT阶段。

2. A公司的张总对当前进度表示满意，但希望我们在下周五前完成移动端的适配工作。

3. 目前项目整体进度约70%，主要风险点是第三方硬件供应商的接口文档迟迟未给，可能影响上线时间，需协调。

4. 另外，A公司的技术对接人李经理下周会到我们这边来做UAT测试，需要安排一下会议室和测试环境。

5. 合同金额350万元，首付款已到账，请确认第二笔款项的开票节点。

详细进度表见附件。

以上，请知悉。
张明
项目经理""",
    ("X智能仓储项目进度报告.pdf", create_pdf_attachment(), "application/pdf"),
)

# 邮件 2：报价单 + Excel 附件
send_email(
    "智能仓储管理系统项目报价单及技术方案（请查收）",
    """尊敬的赵总，您好。

根据上周的交流，我们为贵司（北京华远物流有限公司）制定了智能仓储管理系统建设的详细报价方案，请见附件。

核心方案包括：
1. WMS核心系统 — 350万元
2. 移动终端适配 — 120万元
3. 系统集成与调试 — 80万元
4. 培训与技术支持 — 45万元

总报价：595万元（含一年免费维护）。

我方项目负责人王芳（技术总监）和您那边的对接人刘副总可以进一步沟通技术细节。如有任何疑问，随时联系。

此致
张明
商务经理
北京智联科技有限公司""",
    ("华远物流智能仓储报价单.xlsx", create_excel_attachment(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
)

# 邮件 3：会议纪要 + Word 附件
send_email(
    "Re: X智能仓储系统项目周会纪要（2026-07-07）",
    """张总、李经理，你们好。

附上今天下午的项目周会纪要，请查收。

本次会议主要确认了以下几点：

- WMS核心模块进度70%，按计划推进
- 移动端适配需加速，张总要求下周五前完成
- 第三方硬件接口文档延期，已向供应商发催办函
- UAT测试安排7月15日启动，李经理届时到我司驻场
- 合同第二笔款项（30%）应在UAT通过后支付

我方内部负责人：
- 技术：王芳（技术总监）
- 商务：张明（项目经理）
- 驻场：李经理（华远物流技术对接人）

下次会议时间：7月14日 10:00

谢谢大家。

王芳
技术总监
北京智联科技有限公司""",
    ("项目周会纪要_20260707.docx", create_docx_attachment(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
)

# 邮件 4：简单询价（无附件）
send_email(
    "咨询：关于贵司智能仓储方案的几个问题",
    """王总，您好。

我是上海速达供应链管理有限公司的技术负责人陈强，上周在行业展会上了解到贵司的智能仓储解决方案，很感兴趣。

我们目前有3个仓库需要做智能化升级，想了解一下：

1. 贵司的方案是否支持多仓库统一管理？
2. 实施周期大概多久？
3. 是否有与我们现有ERP系统对接的经验？

方便的话请回复或安排一次线上交流。我的联系方式：chenqiang@suda-sc.com，电话 138xxxx1234。

期待您的回复。

陈强
技术负责人
上海速达供应链管理有限公司""",
    None,
)

# 邮件 5：内部协调邮件
send_email(
    "请安排华远物流项目的UAT测试环境",
    """王芳，你好。

华远物流（A公司）的李经理下周一（7月15日）到我们公司来做UAT测试，预计驻场一周。

需要你这边准备：
1. 测试服务器环境（配置参照之前的邮件）
2. 测试数据集（已脱敏的真实数据）
3. 测试用例文档

另外，这个项目的内部负责人之前是刘工，现在刘工调去其他项目组了，后续由你来全权负责华远物流的技术对接。请和张明（项目经理）保持沟通。

这个项目对我们拿下华远后续的二期合同很关键，务必重视。

孙总
技术部负责人""",
    None,
)

print("=" * 55)
print("全部发送完成！请到 Streamlit 中拉取测试。")
print("建议拉取 10 封（默认参数），然后点击处理。")
print("=" * 55)
