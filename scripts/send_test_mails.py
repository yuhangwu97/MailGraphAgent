"""
发送 30 封测试邮件到 yuhangwu1022@gmail.com
覆盖：多项目、多公司、多人员、任务/事件/合同/风险/会议等场景
附件类型：PDF、Excel、Word、CSV、TXT、ZIP（模拟）
"""
import smtplib
import os
import io
import zipfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
USER = os.environ.get("TEST_MAIL_USER", "yuhangwu1022@gmail.com")
PASS = os.environ.get("TEST_MAIL_PASS", "")
TO = os.environ.get("TEST_MAIL_TO", "yuhangwu1022@gmail.com")

# ═══════════════════════════════════════
# 附件工厂
# ═══════════════════════════════════════

def _zip_str(name: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr(name, content)
    return buf.getvalue()

def make_pdf(title: str = "项目进度报告") -> bytes:
    return f"""%PDF-1.4
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
%%EOF""".encode()

def make_xlsx(title: str = "报价单", rows: list[list[str]] = None) -> bytes:
    """生成合法的 OOXML xlsx 文件（RAGFlow pandas/openpyxl 可解析）。"""
    if rows is None:
        rows = [["项目名称","数量","单位","金额(元)"],
                ["智能仓储系统WMS","1","套","3,500,000"],
                ["移动终端适配","50","台","1,200,000"]]

    # 构建 sheet1.xml（inlineStr，避免 sharedStrings 复杂度）
    sheet_rows = []
    for i, row in enumerate(rows):
        cells = "".join(
            f'<c r="{chr(65+j)}{i+1}" t="inlineStr"><is><t>{v}</t></is></c>'
            for j, v in enumerate(row))
        sheet_rows.append(f'<row r="{i+1}">{cells}</row>')

    files = {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>''',
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''',
        "xl/workbook.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="{title}" sheetId="1" r:id="rId1"/></sheets>
</workbook>''',
        "xl/_rels/workbook.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>''',
        "xl/worksheets/sheet1.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>''',
    }
    return make_zip(files)

def make_docx(title: str, body: str) -> bytes:
    doc = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{title}</w:t></w:r></w:p>
{"".join(f'<w:p><w:r><w:t>{line}</w:t></w:r></w:p>' for line in body.split("\\n"))}
</w:body></w:document>'''
    return _zip_str("document.xml", doc)

def make_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    lines = [",".join(headers)] + [",".join(r) for r in rows]
    return "\n".join(lines).encode("utf-8-sig")

def make_txt(content: str) -> bytes:
    return content.encode("utf-8")

def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()

# ═══════════════════════════════════════
# 发送工具
# ═══════════════════════════════════════

_sent = 0

def mail(subject: str, body: str, attachment: tuple[str, bytes, str] | None = None,
         from_name: str = "", from_addr: str = ""):
    global _sent
    msg = MIMEMultipart()
    addr = from_addr or USER
    sender = f"{from_name} <{addr}>" if from_name else addr
    msg["From"] = sender
    msg["To"] = TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if attachment:
        fname, data, mime = attachment
        part = MIMEBase(*mime.split("/", 1))
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(USER, PASS)
        smtp.send_message(msg)
    _sent += 1
    tag = "📎" if attachment else "✉️"
    print(f"   [{_sent:02d}] {tag} {subject[:55]}")


# ═══════════════════════════════════════
# 30 封邮件
# ═══════════════════════════════════════

print("=" * 60)
print(f"发送 30 封测试邮件 → {TO}")
print("=" * 60)

# ── 项目：华远物流 · X智能仓储系统 (WMS) ──

mail(
    "关于X智能仓储系统升级项目的进度汇报",
    """王工，您好。

向您汇报X智能仓储系统升级项目最新进展：

1. 本周完成WMS核心模块联调测试，系统运行稳定，预计7月20日进入UAT阶段。
2. A公司张总对进度表示满意，但希望下周五前完成移动端适配。
3. 目前整体进度约70%，主要风险是第三方硬件供应商接口文档未提供，可能影响上线。
4. A公司技术对接人李经理下周来我司做UAT测试，需安排会议室和测试环境。
5. 合同金额350万元，首付款已到账，请确认第二笔款项开票节点。

详细进度表见附件。以上，请知悉。
张明
项目经理""",
    ("X智能仓储项目进度报告_20260709.pdf", make_pdf("X智能仓储系统项目进度报告"), "application/pdf"),
    from_name="张明",
)

mail(
    "智能仓储管理系统项目报价单及技术方案（请查收）",
    """尊敬的赵总，您好。

根据上周交流，为贵司（北京华远物流有限公司）制定智能仓储管理系统建设方案，见附件。

核心方案：
1. WMS核心系统 — 350万元
2. 移动终端适配 — 120万元
3. 系统集成与调试 — 80万元
4. 培训与技术支持 — 45万元
总报价：595万元（含一年免费维护）。

我方项目负责人王芳（技术总监）和贵司刘副总可进一步沟通技术细节。

此致
张明 商务经理""",
    ("华远物流智能仓储报价单.xlsx", make_xlsx("华远物流智能仓储报价单", [
        ["项目名称","数量","单位","单价(万元)","金额(万元)"],
        ["WMS核心系统","1","套","350","350"],
        ["移动终端适配","50","台","2.4","120"],
        ["系统集成与调试","1","次","80","80"],
        ["培训与技术支持","15","人天","3","45"],
        ["合计","","","","595"],
    ]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    from_name="张明",
)

mail(
    "Re: X智能仓储系统项目周会纪要（2026-07-07）",
    """张总、李经理，你们好。

附上今天下午项目周会纪要，请查收。

确认要点：
- WMS核心模块进度70%，按计划推进
- 移动端适配需加速，张总要求下周五前完成
- 第三方硬件接口文档延期，已向供应商发催办函
- UAT测试7月15日启动，李经理届时驻场
- 合同第二笔款项（30%）UAT通过后支付

内部负责人：技术王芳、商务张明、驻场李经理
下次会议：7月14日 10:00

王芳 技术总监""",
    ("项目周会纪要_20260707.docx", make_docx("X智能仓储系统项目周会纪要",
        "会议时间：2026年7月7日 14:00-15:30\\n参会人员：张伟(A公司)、王芳(我方)、李经理(A公司技术)\\n"
        "1. 项目进度：WMS核心模块已完成70%，移动端适配预计下周五完成。\\n"
        "2. 风险点：第三方硬件接口文档未提供，可能影响联调进度。\\n"
        "3. 下一步：安排UAT测试环境搭建，预计7月15日前完成。\\n"
        "4. 合同金额确认：项目总额350万元，首付款30%已到账。"),
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    from_name="王芳",
)

mail(
    "【任务分配】华远物流UAT测试环境准备",
    """王芳，你好。

华远物流李经理下周一（7月15日）到我司做UAT测试，预计驻场一周。

需要你准备：
1. 测试服务器环境
2. 测试数据集（已脱敏真实数据）
3. 测试用例文档

华远项目之前由刘工负责，现刘工调去其他项目组，后续你全权负责技术对接。
请和张明保持沟通。这个项目对我们拿下二期合同很关键，务必重视。

孙总 技术部负责人""",
    from_name="孙总",
)

mail(
    "【风险预警】华远项目第三方硬件接口延期",
    """张明、王芳，

华远项目的第三方硬件供应商（深圳凯联科技）至今未提供接口文档，已延期2周。
按合同约定逾期超过15天可以触发违约条款，但考虑到后续合作，建议先沟通协调。

方案建议：
A）发正式催办函，给最后期限7月20日
B）同步启动备选供应商评估（广州力通、武汉智远）

请尽快确认方向，我会安排法务起草催办函。

孙总""",
    from_name="孙总",
)

mail(
    "【会议邀请】7月14日华远项目周会",
    """张明、王芳、李经理，

定于7月14日（周一）10:00-11:30 在华远物流3楼会议室召开项目周会。

议程：
1. WMS核心模块进展回顾
2. 移动端适配进度
3. 第三方接口风险应对
4. UAT测试计划确认
5. 二期项目初步沟通

请提前准备好各自模块的进度数据。会议室已预订，如有冲突请提前告知。

张总 华远物流项目负责人""",
    from_name="张总",
)

# ── 项目：速达供应链 · ERP对接 ──

mail(
    "咨询：关于贵司智能仓储方案的几个问题",
    """王总，您好。

我是上海速达供应链管理有限公司技术负责人陈强，上周行业展会上了解到贵司智能仓储方案，很感兴趣。

我们目前有3个仓库需要智能化升级：
1. 方案是否支持多仓库统一管理？
2. 实施周期大概多久？
3. 是否有与我们现有ERP系统对接的经验？

方便的话请安排一次线上交流。chenqiang@suda-sc.com，电话 138xxxx1234。

陈强 技术负责人 上海速达供应链管理有限公司""",
    from_name="陈强",
)

mail(
    "【新项目】速达供应链ERP对接项目启动",
    """各位好，

经上周与上海速达供应链（陈强、赵总）两轮沟通，确认启动ERP对接项目。

项目概况：
- 客户：上海速达供应链管理有限公司
- 范围：对接其现有用友ERP U8系统，实现仓储数据实时同步
- 周期：预计3个月
- 合同金额：120万元
- 我方负责人：张明（项目经理）、王芳（技术）

请张明本周内出项目计划书，王芳评估技术方案和人力投入。

孙总""",
    from_name="孙总",
)

mail(
    "速达供应链ERP对接技术方案（初稿）",
    """张明，你好。

附件是速达供应链ERP对接的技术方案初稿，主要要点：

1. 采用中间件模式对接用友U8，避免直接耦合
2. 接口标准：RESTful API + WebService 双通道
3. 数据同步：库存、出入库单、盘点结果 三向同步
4. 预估人力：后端2人 + 前端1人，约8周开发 + 4周联调

请审阅，明天晨会讨论。

王芳""",
    ("速达ERP对接技术方案_v1.pdf", make_pdf("速达供应链ERP对接技术方案"), "application/pdf"),
    from_name="王芳",
)

mail(
    "速达项目合同条款确认",
    """张明，

速达合同我看了，有几个条款需要和赵总确认：

1. 二期维护费用从第二年开始计算，不是第三年——合同里写的是第三年
2. 数据安全责任条款需要补充，建议增加数据泄露赔偿上限
3. 验收标准需细化，目前"系统正常运行"太模糊

请今天下午和赵总电话确认，确认后我让法务改。

孙总""",
    from_name="孙总",
)

# ── 项目：鹏程电子 · 智能制造平台 ──

mail(
    "【商务拓展】深圳鹏程电子科技有限公司来访安排",
    """张明，

深圳鹏程电子科技有限公司林工（CTO）和周经理（项目总监）下周三（7月16日）来访考察。

鹏程电子是华南区最大的电子元器件制造商，年产值50亿+，目前有3个工厂、12个仓库需要智能化改造。潜在合同金额预估800万+。

请准备：
1. 公司介绍PPT（重点突出制造业案例）
2. 华远项目作为最近案例
3. 技术Demo环境

来访行程安排：10:00-12:00 公司参观+方案介绍 / 12:00-13:30 午餐 / 14:00-16:00 技术交流

务必重视，这是今年最重要的潜在客户之一。

孙总""",
    from_name="孙总",
)

mail(
    "鹏程电子来访接待确认",
    """孙总，已确认鹏程电子来访安排：

- 时间：7月16日（周三）10:00-16:00
- 来访人员：林工（CTO）、周经理（项目总监）、黄助理
- 我方参与：您、王芳（技术）、我（商务）、安排行政周婷协助接待
- 午餐预订：公司附近粤菜馆（鹏程是深圳公司）
- Demo环境：王芳已搭建完毕，测试通过

另：建议准备一份我们能提供的制造业解决方案概览文档，我明天整理好发给您审阅。

张明""",
    from_name="张明",
)

mail(
    "鹏程电子智能制造平台初步方案",
    """林工、周经理，你们好。

感谢周三的深入交流。根据贵司提出的需求，我们整理了初步方案，见附件。

方案要点：
1. 多工厂统一管理：支持3个工厂、12个仓库的统一调度
2. 智能制造对接：和贵司现有MES系统数据打通
3. 分阶段实施：先1个工厂试点（3个月），再推广到全部（额外6个月）
4. 预算估算：首期试点约280万，全推广约850万

如有疑问随时沟通。期待合作！

张明 项目经理 北京智联科技有限公司""",
    ("鹏程电子智能制造平台方案.pdf", make_pdf("鹏程电子智能制造平台初步方案"), "application/pdf"),
    from_name="张明",
)

mail(
    "鹏程项目技术评估补充——MES对接可行性",
    """张明，

关于鹏程电子MES系统对接，我做了进一步评估：

1. 鹏程MES是西门子Simatic IT，有标准API，对接可行
2. 需要额外1名MES领域专家参与（建议外聘或从刘工那边借调）
3. 首期试点建议选深圳龙华工厂（规模适中，管理团队配合度高，已有MES基础）

另外有个风险：鹏程现有MES版本较老（2019版），API文档不全。我建议先做一次现勘，确认版本和接口现状后再出最终方案。时间预估需要去深圳出差2-3天。

你看要不要安排在7月21-23号？我可以配合。

王芳""",
    from_name="王芳",
)

# ── 项目：越秀贸易 · 跨境物流系统 ──

mail(
    "广州越秀贸易有限公司跨境物流系统需求",
    """张明，你好。

上周和广州越秀贸易的黄总、何经理（物流负责人）有初步接触。越秀是做东南亚跨境电商物流的，日均处理2万+包裹。

他们的痛点：
1. 多口岸清关数据不互通（深圳、广州、南宁三个口岸）
2. 包裹追踪信息割裂，客户投诉多
3. 希望引入自动化仓储分拣系统

这是一个典型的跨境物流+仓储融合项目。黄总表示如果方案合适，预算不设上限。
但竞争激烈——菜鸟和顺丰也在接触。

我已约了7月18日和黄总视频会，请张明一起参加，准备初步方案思路。

王芳""",
    from_name="王芳",
)

mail(
    "越秀跨境物流系统项目立项评审",
    """各位，

定于7月18日14:00进行越秀跨境物流系统项目立项评审。

项目背景：广州越秀贸易年跨境包裹量约800万件，现有3个口岸仓库（深圳、广州、南宁），需统一管理平台。

初步估算：
- 合同规模：约500-600万元
- 周期：6-8个月
- 人力：后端3人+前端2人+项目经理1人

请在会前评估各自团队人力可用情况。这个项目时间紧（越秀希望10月上线），需决策是否接。

孙总""",
    from_name="孙总",
)

mail(
    "越秀项目竞品分析和差异化方案",
    """张明、孙总，

针对越秀项目竞争对手（菜鸟、顺丰）做了初步分析：

菜鸟优势：品牌、物流网络、资金
菜鸟劣势：定制化能力弱、SaaS标准化方案难以适配越秀特殊需求

顺丰优势：物流实操经验、仓储运营能力
顺丰劣势：软件能力一般、报价偏高

我们的差异化策略：
1. 强调"定制化+快速交付"——10月上线，比菜鸟快2个月
2. 突出"多口岸数据互通"能力——我们已有类似案例（虽然规模不同）
3. 价格有竞争力——预估500-600万 vs 顺丰800万+

建议7月18日视频会时重点展示多口岸方案Demo。

王芳""",
    from_name="王芳",
)

# ── 内部事务 ──

mail(
    "【通知】7月份技术部全员季度总结会",
    """技术部全体同事，

定于7月25日（周五）14:00-17:00 在3楼大会议室召开Q2季度总结会。

议程：
14:00-14:30 各项目进展汇报（WMS/ERP/鹏程/越秀）
14:30-15:00 技术分享：王芳 — 中间件架构最佳实践
15:00-15:30 Q&A + 团队讨论
15:30-16:00 下季度规划
16:00-17:00 自由交流 + 茶歇

请各项目负责人提前准备3-5页PPT。全员必须参加，特殊情况请提前请假。

孙总 技术部负责人""",
    from_name="孙总",
)

mail(
    "【请假申请】7月21-23日深圳出差",
    """孙总，您好。

申请7月21-23日（周一到周三）赴深圳鹏程电子现勘，确认MES系统版本和接口情况。

同行：王芳
目的：鹏程电子龙华工厂MES系统现勘
预计费用：差旅约4000元（机票+住宿+餐补）

已和张明确认不影响华远项目进度（UAT那周由他现场支持）。

请审批。

王芳""",
    from_name="王芳",
)

mail(
    "【报销申请】华远项目UAT测试环境采购",
    """赵明，你好。

华远项目UAT测试环境需要采购以下设备：

1. 测试服务器 Dell R750xs ×1 — 约4.5万
2. 交换机 H3C S5560X ×1 — 约1.2万
3. 测试用PDA扫码枪 ×5 — 约2.5万
合计：约8.2万元

已获孙总口头批准，正式PO见附件。请安排采购流程。

张明""",
    ("华远UAT设备采购申请.xlsx", make_xlsx("UAT测试环境采购清单", [
        ["设备名称","型号","数量","单价(元)","金额(元)"],
        ["测试服务器","Dell R750xs","1","45,000","45,000"],
        ["交换机","H3C S5560X","1","12,000","12,000"],
        ["PDA扫码枪","Zebra TC21","5","5,000","25,000"],
        ["合计","","","","82,000"],
    ]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    from_name="张明",
)

mail(
    "【HR通知】新员工入职培训安排",
    """各位，

本周五（7月11日）有3位新同事入职，已安排入职培训：

09:30-10:30 公司介绍 & 规章制度（周婷）
10:30-12:00 技术架构培训（王芳）
14:00-15:00 安全培训（IT部）
15:00-16:00 项目流程介绍（张明）

新同事：
- 赵阳 — 后端开发（加入华远项目组）
- 陈丽 — 前端开发（加入速达项目组）
- 黄伟 — 测试工程师

请各mentor提前准备。另：新同事电脑和工位已准备好，周婷确认。

周婷 HR""",
    from_name="周婷",
)

# ── 里程碑 & 事件 ──

mail(
    "【里程碑】华远WMS核心模块UAT测试通过！",
    """各位同事，

很高兴通知大家：华远物流X智能仓储系统WMS核心模块于今天（7月22日）正式通过UAT测试！

关键数据：
- 测试用例：186项
- 通过率：97.8%（183/186）
- 遗留问题：3个（均为低优先级UI优化，不影响上线）
- 客户满意度：李经理评价"超出预期"

感谢团队的努力！特别感谢：
- 王芳：连续三周加班攻坚
- 张明：协调客户和供应商
- 新同事赵阳：快速上手，独立完成报表模块

下一步：7月28日正式上线，请大家继续保持。

孙总""",
    from_name="孙总",
)

mail(
    "华远项目二期意向沟通",
    """张明，

今早和A公司张总通了电话，对方对一期项目非常满意，主动提出二期计划：

1. 新增2个仓库（天津、武汉）纳入WMS管理
2. 增加运输管理模块（TMS）
3. 引入AI库存预测功能
4. 预估二期规模约400-500万

张总希望在8月初安排一次二期方案沟通会。我觉得这是很好的信号——一期还没上线二期就来了。

请和王芳碰一下，评估二期人力需求。另外也要考虑我们其他项目（速达、鹏程、越秀）的并行能力。

孙总""",
    from_name="孙总",
)

mail(
    "【合同签署】速达供应链ERP对接合同已签",
    """张明、王芳，

速达供应链ERP对接项目合同今天正式签署。赵总签字了。

合同要点：
- 金额：120万元
- 周期：3个月（8月1日-10月31日）
- 付款：签约30% / 中期40% / 验收30%
- SLA：系统可用性99.5%，故障响应<2小时

张明请安排项目启动会（建议下周一），确定里程碑计划和资源分配。
王芳请确认技术团队配置（之前计划后端2人+前端1人）。

另外，速达项目的数据安全条款这次加得很严，注意代码和数据处理合规。

孙总""",
    from_name="孙总",
)

mail(
    "【风险解除】华远第三方硬件接口已交付",
    """张明、王芳，

今天收到深圳凯联科技发来的硬件接口文档（v2.1），我和王芳初步审阅，接口完整可对接。

压在华远项目上的最大风险终于解除了。虽然延期了3周，但整体上线时间不受影响（因为UAT也接近尾声）。

后续：王芳安排联调，预计7月25日前完成，7月28日正式上线不变。

悬着的心可以放下来了。

孙总""",
    from_name="孙总",
)

# ── 多附件 & 数据文件 ──

mail(
    "华远项目测试数据集及用例（7月批次）",
    """王芳，

附件是本月UAT测试数据集和用例文档，包含：

1. 测试数据集CSV（500条模拟库存数据）
2. 测试用例Excel（186项）
3. UAT测试报告模板Word

请按这些数据跑一轮完整回归。重点关注新加的报表模块（赵阳负责的部分）。

张明""",
    ("华远UAT测试数据_202607.csv", make_csv(
        ["SKU","品名","库存量","仓库","库位","最后更新"],
        [["SKU-001","电阻器10KΩ","5000","北京A库","A-01-03","2026-07-09"],
         ["SKU-002","电容器100μF","3200","北京A库","A-02-01","2026-07-08"],
         ["SKU-003","MCU芯片STM32","800","北京A库","B-01-05","2026-07-09"],
         ["SKU-004","PCB板4层","1200","北京A库","B-03-02","2026-07-07"],
         ["SKU-005","电源模块5V","2100","北京A库","C-01-01","2026-07-09"],
         ["SKU-006","传感器套件","450","北京B库","A-01-01","2026-07-06"],
         ["SKU-007","连接器套件","3200","北京B库","A-02-03","2026-07-09"],
         ["SKU-008","显示屏LCD","600","北京B库","B-01-02","2026-07-08"],
         ["SKU-009","电机驱动器","350","北京B库","C-02-01","2026-07-09"],
         ["SKU-010","通信模块4G","780","北京B库","C-03-01","2026-07-07"]]),
     "text/csv"),
    from_name="张明",
)

mail(
    "速达ERP对接接口文档及示例数据",
    """王芳，你好。

整理了速达供应链ERP对接所需资料，打包在附件：

- 用友U8接口规范摘要
- 我方API设计初稿
- 10条示例业务数据（JSON）

重点看"库存同步"和"出入库单"两个接口——这是速达最关心的。

另外，速达的ERP版本是U8+ 16.0，和我们之前对接过的版本（13.0）有差异。我标注了几个不兼容点，需要你在方案中注意。

张明""",
    ("速达ERP对接资料包.zip", make_zip({
        "U8_API_规范摘要.txt": make_txt("用友U8+ 16.0 接口规范摘要\n1. 库存查询接口: /api/inventory/query\n2. 出入库单接口: /api/stock/movement\n3. 认证方式: OAuth 2.0\n4. 数据格式: JSON"),
        "我方API设计_v1.txt": make_txt("速达ERP对接API设计\nPOST /api/v1/inventory/sync\nPOST /api/v1/order/inbound\nPOST /api/v1/order/outbound\nGET /api/v1/stock/check"),
        "示例数据.json": make_txt('{"inventory":[{"sku":"SK-001","qty":500,"warehouse":"上海1号仓"}]}'),
        "不兼容点说明.txt": make_txt("U8+ 16.0 vs 13.0 差异\n1. 认证方式从Basic Auth变为OAuth2.0\n2. 分页参数不同\n3. 字段命名从snake_case变为camelCase"),
    }), "application/zip"),
    from_name="张明",
)

# ── 客户反馈 & 问题 ──

mail(
    "华远物流李经理反馈：移动端几个问题",
    """王芳，

李经理今天测试移动端时反馈了几个问题：

1. PDA扫码后偶现2-3秒延迟才显示商品信息（约5次出现1次）
2. 离线模式下，连续扫50件以上后偶尔丢数据
3. PDA屏幕小，字太小看不清——建议调大字号或做适配

前两个是技术问题，需要尽快修复。第三个是UI体验，优先级可以低一些。
李经理说整体体验不错，但这些小问题影响实际使用效率。

请在7月20日前修复1和2，影响UAT效果。

张明""",
    from_name="张明",
)

mail(
    "速达陈强：对技术方案提了几点修改意见",
    """张明，昨天和陈强通了电话，他对方案整体认可，但提了几点修改：

1. 数据同步频率从"每5分钟"改为"实时+定时双模式"——技术上可行，但需要增加消息队列
2. 希望增加一个管理看板（dashboard），展示同步状态和异常告警——前端工作量+2周
3. 要求支持多语言（中英文）——这个好办，前端国际化即可

第1和2点涉及合同变更。我建议第1点接受（技术上有价值），第2点作为二期内容。
你评估一下后和赵总沟通。

王芳""",
    from_name="王芳",
)

# ── 定时任务 & 自动化 ──

mail(
    "华远项目生产环境监控日报配置",
    """王芳、赵阳，

华远项目7月28日正式上线，上线前需要配置生产环境监控。具体要求：

1. 系统健康检查：每5分钟一次，检查CPU/内存/磁盘/数据库连接
2. 接口响应时间：P99 < 2秒 告警
3. 数据同步延迟：> 30秒 告警
4. 日报邮件：每天早上8:00发送前一日运行摘要

监控工具：用我们之前搭建的 Zabbix + Grafana，告警走企业微信+邮件双通道。

另：请赵阳写一个自动化脚本，每天凌晨3:00做一次数据库备份，保留最近7天。

这是上线前的最后一项准备工作，请尽快完成。

张明""",
    from_name="张明",
)

print("=" * 60)
print(f"✅ 全部 {_sent} 封邮件发送完成！")
print("请到 Streamlit → 邮件工作台 → 拉取 → 导入图谱")
print("=" * 60)
