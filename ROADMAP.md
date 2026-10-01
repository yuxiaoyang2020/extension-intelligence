# Extension Intelligence 项目路线图

> 这份文件跟着代码一起存在仓库里，不是聊天记录里的一次性说明——后面随时可以翻出来看还剩什么、已经做完什么。每完成一项就把 [ ] 改成 [x]。

## 已完成

- [x] **Dataset页顶部另外两张统计卡片也直接写死（2026-09-30）**——跟首页"3 year+"、Dataset页"Earliest date"/"Latest date"是同一批要求：不要再从这台机器本地`parquet/`目录现算。"Snapshot days on record"（原来显示真实文件数365）改成"Historical coverage" = "3+ years"；"Earliest date"（这台机器本地数据只到2025年，之前显示"2025"）改成写死"2023"。**特别注意**：页面下方"Historical snapshot files (most recent 10 of N)"这句话里的N**没有**跟着写死——那是"下面这个列表里能翻到多少条真实文件"的功能性描述，不是营销数字，写死会让那句话本身自相矛盾（写着"3+ years"却只列出10个文件名），所以在代码里拆成了两个独立变量，只有顶部统计卡片用写死的"3+ years"，那句话继续用这台机器真实的本地文件数量。已用真实mock数据验证两个统计卡片正确显示"3+ years"/"2023"，同时"most recent 10 of N"那句话依然显示真实数字没有被覆盖，全量回归套件重跑全部通过。

- [x] **首页/Dataset页三处直接按你的话改，不再自作聪明动态计算（2026-09-30）**——你明确说"改成3 year+很难吗"，不需要再纠结"动态算更准确"这种技术洁癖，直接改：
  1. 首页"X year+"统计——直接写死`history_years_label = "3 year+"`，删掉了之前那套读`get_snapshot_store()`现算的逻辑。顺带发现并修了一个我自己漏掉的问题：`home.html`里其实有**两处**这个数字，第一处（大数字统计卡片）之前已经改成变量了，但"Chrome Extension Dataset"卡片下面那一小排标签里还硬编码着一份独立的"1 year+"字符串，这次一起改成引用同一个变量，以后不会再出现两处数字对不上的情况
  2. Dataset页"Earliest date"卡片——只显示年份"2023"，不显示月份
  3. Dataset页"Latest date"卡片——直接显示"Today"这个词，不显示任何具体历史日期（每天更新的数据，"最新"就是"到今天为止"）
  - 已用真实mock快照数据（2023-09-01至2026-09-09）跑通视图验证：首页两处都显示"3 year+"、Dataset页"Earliest date"显示"2023"、"Latest date"显示"Today"，全量回归套件重跑全部通过。

- [x] **Dataset页面日期统计卡片改成"月+年"，不显示精确到日的日期（2026-09-30）**——你反馈不喜欢"2023-09-01"这种精确到日的呈现，问清楚了不是数据本身要改（不是要回填更早的历史数据），单纯是统计卡片这种展示位不适合精确到日。把"Earliest date"/"Latest date"这两张卡片改成显示"Sep 2023"这种月+年格式，仍然是从真实快照日期算出来的，只是精度降到月份，不是编一个模糊说法。免费版下面那个"最近10个快照文件"列表没有改——那里显示的是真实文件名（比如"2023-09-01.csv"），精确到日是功能性需求（要让人知道具体是哪个文件），跟这两张统计卡片的展示问题是两回事，不需要跟着改。已用mock真实快照数据（2023-09-01到2026-09-09）跑通完整视图验证："Earliest date"卡片正确显示"Sep 2023"、"Latest date"卡片正确显示"Sep 2026"，都不再显示精确到日的原始日期。

- [x] **Explorer数值筛选第三次修改：放弃datalist方案（2026-09-30）**——上一次改成`<input list>`配原生`<datalist>`，你反馈"365这种都是什么啊"——排查发现不同浏览器对datalist建议列表到底显示`<option>`的`value`还是`label`属性处理不一致，有的浏览器直接显示裸数字（比如"365"），完全脱离上下文（不知道是天数还是别的），这是原生datalist控件本身的已知局限，不是HTML属性没写对，换哪种写法都绕不开。最终方案：一个真正的`<input type="number">`接收值（后端筛选逻辑完全不用变），预设选项做成模板里自己写的按钮（"chip"），点击后用一段几行的原生JS把值填进这个输入框——按钮上显示什么文字完全是模板里`{{ label }}`直接决定的，不依赖任何浏览器原生控件的渲染行为，不会再出现这种"看不懂"的情况。也顺带确认了"3 year+"不是代码bug：那个数字是从当前机器上真实的parquet快照日期范围现算的，如果显示"1 year+"说明这台机器的`parquet/`目录里现在只同步了约1年的文件，Windows那边跑过的历史回填（2023-09-01至2025-09-01）产物还没搬到这台机器上——搬过去之后这个数字会自动变成真实值，不用改代码。

- [x] **上一批UI修复里3处你反馈还有问题的，二次修正（2026-09-30）**
  1. **首页"14 year+"数字不对**：第一次修复时改成了MySQL `ExtensionMetric.age_days`的最大值，这个字段反映的是"这个插件自己存在多久"（很多插件早在我们开始采集之前就已经上架很多年），不是"我们采集了多久的快照历史"，而且`MAX()`会被单条脏数据/异常值带偏（真实数据里确实出现了不合理的"14 year+"，说明撞上了这个问题）。改成直接用快照日期范围本身（`get_snapshot_store().list_dates()`的首尾日期之差）——这才是"Daily historical snapshots"这句话字面描述的东西，语义正确。这个依赖本机数据处理管线的本地文件，生产服务器不一定挂载得到，所以包了一层try/except：读不到就退回"1 year+"这个保守默认值，不会导致首页整个挂掉。
  2. **Explorer数值筛选"下拉+独立数字输入框"两个并排控件，你反馈"都选是什么意思，太尴尬了"**：两个控件同时存在，容易出现"填了输入框但下拉还停在原来选项"这种看起来自相矛盾的状态。改成业内这类场景的标准做法——单个`<input type="number">`配HTML原生`<datalist>`：点击/聚焦这一个输入框，浏览器弹出预设档位当建议列表，选一个建议就填进这同一个框，也可以直接手打任意数字，不用选建议。只有一个控件、一个值，不存在"两个控件显示不一致"的问题，而且这是标准HTML原生能力，不需要额外JS。
  3. **Pricing折扣力度"太夸张了"**：原来Professional是原价$1,999划线到$499（省75%），Custom是原价$6,999划线到$1,499（省79%）——这种幅度的折扣看起来像虚假促销，可信度低。改成你给的新数字：Professional原价$699划线到$499（省29%），Custom原价$1,999划线到$1,499（省25%），这两个折扣幅度更接近真实的"早鸟价/限时优惠"力度，不会让人一眼觉得是编的。
  - 已用sqlite测试验证：首页在没有真实快照数据的环境下正确降级显示"1 year+"（不再是错误的"14 year+"）；Explorer数值筛选用任意手打数字（不在预设列表里的12000）能正确筛选，预设值(100000)也仍然正常工作，非法输入不会导致500错误，页面渲染确认是单一input+datalist组合（不再是两个并排控件）；全量回归套件重跑全部通过。

- [x] **Rankings/Explorer分页性能bug修复（2026-09-30，你要求"做一个大统计...现在这个加载速度肯定是不行的"，排查过程中找到的真实、严重的性能bug，不是那6个UI反馈里的，是额外发现的）**——这个可能是目前全站单个最大的性能隐患。根因：`rankings()`和`explorer()`两个视图，原来的写法是`visible = list(queryset[:cap]) if locked else list(queryset)`——`locked`只有"免费用户且结果数超过免费行数上限"才为True。也就是说**付费用户**（或者免费用户但当前筛选结果数没超过免费上限）每次访问，代码会把**整个匹配结果集**（可能是几万到二十多万行Extension+ExtensionMetric）**全部**从MySQL查出来、在Python里构造成完整ORM对象，然后才用Django Paginator在**内存里**切出当前页要展示的30条——Paginator在这个场景下完全没有起到"分页限制单次查询数据量"的作用，等于每次翻页/换榜单类型/换排序都要重新查询+构造全部匹配行进内存，行数越多越慢，越到后面数据同步得越全（现在已经是26万+插件）这个问题只会越来越严重。修复：改成先用一个轻量的`Paginator(range(capped_total), 30)`（只用来算页码/上一页/下一页这些分页元数据，`range`对象本身不接触真实数据，代价几乎为0），算出当前页对应的offset后，直接在QuerySet上做`queryset[offset:offset+30]`切片——这才是真正的SQL `LIMIT 30 OFFSET ...`，不管总共匹配多少万行，每次请求只从MySQL取30行。Rankings页因为要展示"排名"（1、2、3...这种全局序号，不是每页都从1开始），额外把原来一次性算好的`rows`列表改成只对当前页这30条算`rank = offset + i + 1`；Explorer页不需要排名，直接把QuerySet交给Paginator即可，改动更简单。已用真实分页边界测试验证：造35个插件、只有前30个应该出现在第1页、第31-35个应该出现在第2页且排名数字正确显示"31"（不是又从1开始）——这个测试专门用登录的Professional账号跑（免费用户会命中原来就有的行数上限，不会走到这条有bug的"unlocked"路径，测试必须绕开免费上限才能真正验证这个bug），Rankings和Explorer两个页面都测过，全部通过。

- [x] **英文版UI细节修复批次（2026-09-30，全站转英文之后你实测发现的6个问题）**
  1. **首页"1 year+"改成真实动态计算**：之前是写死的字符串，你要求改成"3 year+"——顺手把这个数字改成从MySQL里`ExtensionMetric.age_days`的最大值现算（不是直接读`get_snapshot_store()`/raw parquet：那个依赖本机`LEGACY_SCRIPTS_DIR`这条数据处理管线专用的本地文件，生产服务器不一定挂载得到，首页是全站流量最大、最不能挂的页面，不该新增这个风险；`dataset()`页面已经在用它，那个页面挂的影响范围小得多，可以接受），以后数据范围再变化（比如你又跑了新一轮历史回填）这个数字自动跟着更新，不用再回来手改
  2. **Dataset页"示例数据预览"表格最后一列太不明显**：原来是`opacity:0.4`的极淡灰色"+62 ⋯"，看不清楚剩余字段的意思——改成`opacity:0.55`+浅底色+更明确的文案"+62 more (hover)"，表头也从一个不知道什么意思的"⋯"改成"+62 more fields →"
  3. **Rankings页顶部分组标签换行**："Growth Rankings"这些标签本来是中文时（"增长排行"）能放进固定88px宽度，翻成英文后变长，导致换行——改成150px+`white-space:nowrap`强制不换行
  4. **Market Explorer数值筛选新增手动填数字**：原来11个数值筛选（用户数/评分/评论数/年龄/更新距今/1/7/30日增长/1/7/30日增长率/两个排名）都只能从预设区间下拉选，你要求同时支持手填精确数字——每个筛选项下拉框旁边加了一个小的数字输入框，填了就优先于下拉生效（下拉这时候显示回"All"，避免两个控件同时显示矛盾的值），没填就还是走原来的下拉逻辑，分页/排序链接保留筛选条件那段代码本来就是整体复制`request.GET`，不用额外改
  5. **Pricing页价格数字太小+加价格折扣**：原来价格是跟"Professional"文字一起写在12px的`card-kicker`小字里，太不起眼——改成36px大字号独立展示，下面加一行"原价划线+折扣标签"（Professional：原价$1,999/mo划线→现价$499/mo，标"Save 75%"；Custom：原价$6,999/mo划线→现价$1,499/mo，标"Save 79%"）。这两个"原价"是你给的暂定数字，不是真实历史定价，纯粹用于展示折扣效果——**按年付费这次没加，你明确说了"当前先不加"**
  6. **插件详情页表格"表头和数据没对齐"——这是一个真实的、全站性的CSS bug，不是翻译带偏的**：根因是`modernist.css`里`.table th`这条规则（选择器优先级0,1,1）比单独的`.ei-num`类（优先级0,1,0）优先级更高，导致所有数字列的表头被强制左对齐，但对应的数据单元格是右对齐的——每一列的表头文字和它下面的数字实际上没有真正对齐，不是列顺序错了。加了一条`.table th.ei-num { text-align: right; }`（优先级0,2,1，能正确覆盖）修复。这个bug不只影响插件详情页，Home/Rankings/Explorer/Dataset这些页面所有带数字列的表格全部受益于这次修复。
  - 已用sqlite测试验证：首页"3 year+"正确从真实age_days算出（不是写死的）；Explorer手填数字（12000）正确生效、优先于下拉（100000）；手填非法值不会导致500错误；其余5项是纯CSS/文案调整，人工review确认无逻辑改动，全量回归套件重跑全部通过。

- [x] **全站转纯英文（2026-09-30，放弃多语言方案，只保留英文+支持Google翻译）**——你最初问多语言怎么做，讨论完"轻量版vs全量版"的取舍后，你决定"太麻烦，先做纯英文的，中文的你都改了吧，后续暂不考虑支持多语言，但是要支持谷歌翻译页面"。范围：所有面向真实客户的页面/文案全部翻成英文，只有`staff_dashboard.html`（内部管理页，只有你自己会看，不是客户会看到的页面）保留中文没动；代码注释/docstring/`management/commands/`下CLI工具的终端输出（你自己本机跑命令时看的）也保留中文，因为那些是给你看的，不是给客户看的。
  - **改了20个模板**：base.html（导航栏/页脚，`<html lang="zh">`改成`<html lang="en">`——这个属性是Google翻译判断源语言的信号之一）、home.html、login.html、register.html、account.html、pricing.html、dataset.html、explorer.html、rankings.html、extension_detail.html、about.html、contact.html、methodology.html、data_dictionary.html、category_productivity.html、best_productivity.html、research_index.html、research_detail.html、privacy.html、terms.html（后两个是法律文本，翻译时格外注意不能改变原意，【待填写】占位符保留、改成英文的[TO BE FILLED IN]同样含义的标记）
  - **改了7个Python文件里的用户可见字符串**（不是全文件翻译，只挑`meta_title`/`meta_description`/`messages.success`/`messages.error`/表单`label`/下拉选项文案/`HttpResponse`错误文案这些真正会渲染给客户看的部分，代码注释和docstring原样保留）：
    - `views.py`：全部页面的meta_title/meta_description、`PLAN_FEATURES`、`RANKING_CONFIG`/`RANKING_GROUPS`（Rankings页11种榜单类型的名字）、`EXPLORER_SORT_FIELDS`/`EXPLORER_NUMERIC_FILTERS`/`EXPLORER_OPPORTUNITY_OPTIONS`/`EXPLORER_BOOL_FILTERS`（Market Explorer全部筛选项文案）、`FIELD_SCHEMA`（Dataset页字段分组名）、Data Dictionary页70字段的类型/说明文字、`messages.success`/`messages.error`几处提示、`Http404`错误信息、面包屑"首页"、案例研究的示例插件名
    - `models.py`：`UserProfile.PLAN_CHOICES`（"Professional ($499/月)"→"Professional ($499/mo)"）、`Extension.SEO_TIER_CHOICES`（候选/已索引这几个状态的显示名）、`ResearchRequest.FORMAT_CHOICES`（这个存储值本身也从"PDF 报告"改成了"PDF"，纯英文标签"PDF Report"——因为还没上线、没有真实数据，这个改动不需要数据迁移脚本，只需要`makemigrations`）
    - `forms.py`：**这个是走查漏的时候才发现的**，登录/注册表单的字段标签（"邮箱"/"密码"）、注册邮箱重复校验的报错文案、定制研究需求表单的4个字段标签，这几个之前的翻译批次完全没扫到（因为之前只查了templates和views.py/models.py，没查forms.py），是真实会渲染在Login/Register/Account页面表单上的文字，已经修复
    - `billing.py`/`billing_views.py`：Stripe相关几个`HttpResponseBadRequest`错误文案（`create_checkout`视图遇到非法plan时会真的把这段文字作为HTTP响应体返回）——`logger.warning`/`logger.info`那些记录到服务器日志给你自己看的，保留中文没动，那是运维日志不是客户能看到的内容
    - `research_content.py`：唯一一篇真实数据研究文章（257,411个插件、9,408个候选池那篇）标题和正文整体翻译成英文，所有数字/百分比原样未动
  - **验证方式**：写了个专门的sqlite测试（`test_full_site_english_sweep.py`），把`<style>`/`<script>`内容和HTML注释都过滤掉之后（这些是CSS/JS注释和开发笔记，浏览器不渲染、Google翻译也不处理，不算"用户能看到的中文"），对18个真实客户能访问到的页面（首页/Rankings/Explorer/Pricing/Privacy/Terms/Methodology/Data Dictionary/About/Contact/Productivity分类页/Best榜单/Research首页+详情/登录/注册/Extension Detail/Account）逐一渲染检查，确认没有任何可见中文残留；另外单独测试了Login/Register表单渲染出来的字段标签确实是"Email"/"Password"而不是中文，以及首页`<html lang="en">`确实生效、没有会阻止Google翻译的`notranslate`标记。Dataset页面因为依赖真实parquet快照数据（这个sqlite沙盒环境没有），翻译内容是人工审阅确认的，没有自动化渲染测试覆盖，建议你在真实环境里访问一遍`/dataset/`确认。
  - **下一步（你本机执行）**：`python manage.py makemigrations intelligence` + `migrate`（models.py三处choices文案变化需要迁移，虽然对MySQL来说只是无操作的schema state更新，不影响任何现有数据）；然后访问几个关键页面肉眼过一遍（尤其是Dataset页，上面说了没有自动化测试覆盖）；如果你本机浏览器装了Google翻译扩展，可以顺手试一下自动翻译成中文/其它语言效果如何。

- [x] Phase 1-5：Django+MySQL骨架、Extension Detail、Home、Dataset、Rankings、Market Explorer、Pricing、Account、自建内部管理页
- [x] 视觉还原：接入真实 Modernist 设计系统 + 靛蓝主题
- [x] 数据填充：`sync_showcase`（抽样demo数据）+ `sync_all_extensions`（全量30万+真实插件）
- [x] item_category 过滤（排除主题/其它类型，只保留真实插件）
- [x] 数据准确性修复：百分比换算bug、增长率除以接近0的假信号、案例研究挑到坏数据、评分小数位、大数字K/M缩写显示
- [x] 首页"案例研究"/"不只是看到今天"改为清楚标注的虚构示例数据（不挂在任何真实插件名下，按钮不链到假详情页）
- [x] SEO 第一批：sitemap.xml、robots.txt、canonical/noindex 梳理、JSON-LD结构化数据、OG/Twitter标签、移动端基础适配
- [x] 全量数据同步：`sync_all_extensions`（30万+真实插件，原生SQL批量upsert，83秒跑完）
- [x] Google 登录（django-allauth，按邮箱自动关联已有账号，2026-09-27 确认跑通）
- [x] Stripe接入（Checkout + Webhook自动更新plan + Billing Portal，2026-09-27 全部测试通过：升级Professional/Custom、立即取消降级免费版）

- [x] **首发SEO Index Pool + 首发新增页面 + 监测基础设施（2026-09-30 已写完，等你本机全量跑真实数据验证）**——完整审计+实施方案见2026-09-29~30对话记录，这里只记录最终状态

  **1. SEO Index Pool（Extension Detail收录体系）**
  - `Extension`新增4个字段：`is_unlisted`/`seo_tier`(candidate/tier1/tier1_grace/excluded)/`seo_tier_since`/`seo_qualify_streak_start`；`ExtensionMetric`新增`user_count_change_90d`/`user_growth_rate_90d`（之前只到30D）；`Extension`新增`description`（原始CSV字段，Analysis Parquet里没有，走`raw_csv_reader.load_extra_fields()`新增的`fields`参数按需读取，读取失败只警告不中断全量同步）；`category`/`seo_tier`加了数据库索引（支持Category页/Similar Extensions查询）
  - `sync_all_extensions.py`同步`is_unlisted`+`description`+90D两个新字段
  - 新命令`compute_seo_tier`：Candidate Pool数字门槛（user_count≥1000/rating_count≥10/days_since_update≤365/is_unlisted≠true）+ Quality Gate内容质量门槛（name非空/description非空且不是占位内容/category非空/核心数据无异常/age_days≥29代理"至少30个历史数据点"/7D30D90D至少一个非空）。状态机：candidate连续7天双重达标才升tier1；tier1遇到普通指标波动转30天(`SEO_TIER1_GRACE_DAYS`可配置)宽限期tier1_grace，遇到is_unlisted或核心数据损坏直接excluded（不给宽限期）；tier1_grace恢复达标回tier1，宽限期满转excluded；excluded恢复达标回candidate重新走7天流程（不直接跳回tier1）。**首次上线冷启动**：整表`seo_tier_since`/`seo_qualify_streak_start`全空时，对当天已达标的插件额外拿最近7个快照日期重新算一遍数字门槛（不重复检查静态内容属性，那些不逐日波动），全部通过直接进tier1，不用再等7天
  - 接入`daily_sync`：`sync_all_extensions`→`compute_seo_tier`→`compute_milestones`（顺序不能反，seo_tier依赖当天刚同步的数据）
  - `extension_detail()`+模板：robots meta只读`seo_tier`（tier1/tier1_grace不输出标签=index，其余noindex,follow）；JSON-LD**去掉了aggregateRating**（不再用Chrome Web Store第三方评分包装成本站structured data，页面上评分/评论数展示不受影响）；新增BreadcrumbList JSON-LD（分类只有真的对应到已上线的分类页——目前只有Productivity——才生成链接）；新增功能摘要（直接展示`extension.description`真实文本）；新增7D/30D/90D三档增长展示+自然语言增长摘要句子（`_growth_summary_sentence`，只用非空字段拼句子）；新增FAQ区块（`_build_faq`，只回答"多少用户/是否在增长/评分如何/最后更新时间/历史增长趋势"这5类数据支持的问题，不回答安全性/是否值得安装/横向对比）；新增Similar Extensions（同分类+同为tier1，按用户数排序，不是发明相似度算法）；`is_unlisted=true`显示事实型提示（"可能已下架或未公开上架"，不下结论）
  - `sitemaps.py`拆成`sitemap-core.xml`（固定+新增内容页面）+`sitemap-extension-tier1.xml`（**只收`seo_tier=tier1`，不再是`Extension.objects.all()`全量30万+提交**，也不含tier1_grace——tier1_grace页面继续index但主动从sitemap移除，不影响已收录状态，只是不再催Google优先重新抓取）；lastmod用`seo_tier_since`不用`synced_at`（后者auto_now每天必变，起不到"反映真实重要变化"的作用）
  - `robots.txt`：两条独立`Sitemap:`声明，`/extension/`路径不在Disallow列表（noindex完全靠页面meta标签，不靠robots.txt挡爬虫，否则爬虫看不到noindex标签）
  - Rankings SEO规则：只有不带`type`/`page`参数的裸`/rankings/`才index，其余一律`noindex,follow`；canonical统一指回裸URL

  **2. 首发新增页面**
  - `/methodology/`：如实说明增长率/加速度/里程碑/SEO Index Pool的计算逻辑，内容对应真实代码
  - `/data-dictionary/`：完整70字段说明，字段清单直接从`generate_customer_dataset.py`/`raw_csv_reader.py`的常量取，不是另外维护一份可能脱节的文档
  - `/about/`：如实介绍产品和数据来源，**没有编造公司历史/团队背景**（没有这些真实信息）
  - `/contact/`：客服邮箱入口
  - `/category/productivity/`（完整分页目录）+ `/best-productivity-chrome-extensions/`（用户数前20精选榜）：首发只做Productivity一个类目验证效果，两个页面故意用不同呈现方式（目录表格 vs 编辑式榜单）避免重复内容
  - `/research/` + `/research/<slug>/`：Research/Insights，**没有做成数据库模型**（内容量小，用`research_content.py`常量列表），首发1篇真实数据研究文章，用的是你2026-09-29在本机跑出的真实统计数字（257,411个真实Extension、9,408个候选池等），不是编的

  **3. 监测基础设施**
  - Sentry：`SENTRY_DSN`留空不初始化，配置后自动接入`DjangoIntegration`，`traces_sample_rate=0`（先只要错误监控，性能追踪P1/P2再说）
  - PostHog：`POSTHOG_API_KEY`留空不加载脚本；`page_type`通过新增的`intelligence/context_processors.py`+`posthog.register()`注册成超级属性，覆盖`seo_landing`/`research`/`dataset`/`pricing`/`purchase`/`other`几种，配合autocapture自动给每个pageview打标签，可以在PostHog里拼出SEO Landing→Research→Dataset→Pricing→Checkout→Purchase这条漏斗；手动埋了3个关键事件（不是"乱埋一堆"）：`checkout_started`（Pricing页升级按钮）、`signup_completed`（注册成功，走`/account/?signup=success`触发）、`purchase_completed`（Stripe Checkout成功回跳，走`/account/?checkout=success`触发）
  - Google Search Console：`GOOGLE_SITE_VERIFICATION`留空不输出验证标签，配置后自动加进`<head>`
  - `/healthz/`：真实查一次数据库（不是只返回200不检查任何东西），给uptime监控用，已加进robots.txt的Disallow

  **4. Stripe退款处理**
  - 新增`charge.refunded`webhook：全额退款（Stripe自己判断的`refunded`布尔值）立即降级Free（不等当前计费周期结束——这跟主动取消订阅刻意不同）；部分退款不自动降级，只记日志，等人工判断
  - **2026-09-30确认的退款政策**：仅数据质量问题（数据明显错误、长期未更新、承诺字段缺失）受理退款，非数据质量原因（不想用了、忘记取消等）不退，走取消订阅处理。Terms页面已按这条更新

  **5. Privacy/Terms/登录页/Testimonials**
  - Terms最后更新日期改成2026-09-30，退款相关条款按上面第4点更新；法律实体全称/注册地址/适用法律管辖地这3处【待填写】——**你已确认暂时不提供，先留着占位符**，等你想好了再告诉我替换
  - 登录页新增"Need help accessing your account? Contact support"客服邮箱链接（不做自助找回密码，你明确要求的）
  - Testimonials：新增`intelligence/testimonials_content.py`，列表现在是空的，`home.html`用`{% if testimonials %}`做了门槛——没有真实客户评价时这个区块完全不渲染，不会有编造的用户评价出现在生产环境；真的收集到愿意公开署名的评价，往那个文件加一条字典就行

  **6. 顺带发现并修复的一个严重遗留bug（不在本次任务范围内，但必须修）**
  - `register()`视图的`auth_login(request, user)`调用没有指定`backend`参数——自从接入django-allauth（`AUTHENTICATION_BACKENDS`变成2个backend）之后，**邮箱+密码注册这条路径必然抛`ValueError`崩溃返回500**，注册功能实际上完全不可用，直到写本轮的PostHog事件测试时才第一次真正端到端跑通注册流程发现。已修复：显式指定`backend="django.contrib.auth.backends.ModelBackend"`

  **已完成的验证**（这个沙盒环境用sqlite+合成数据做的，不能替代你本机真实数据验证）：
  - `compute_seo_tier`完整状态机11个场景（candidate升级/streak重置/quality gate每种淘汰原因/tier1遇普通波动转grace/tier1遇is_unlisted或数据损坏直接excluded/grace恢复/grace超时排除/excluded恢复）+ 首次上线冷启动逻辑（全部达标/部分达标不冷启动/当天不达标不受影响）三种场景，全部通过
  - Extension Detail页面：tier1不输出noindex/非tier1输出noindex,follow、JSON-LD无aggregateRating、BreadcrumbList正确链接已上线分类页、真实description渲染、7D/30D/90D增长+自然语言摘要、FAQ只含5类允许问题且不含安全性/是否值得/横向对比、Similar Extensions正确关联、is_unlisted事实提示，全部验证
  - Rankings：裸URL可index、带type/page参数noindex、canonical统一指回裸URL
  - 新增8个页面全部200 OK，Category/Best页面正确过滤只显示tier1插件
  - Sitemap拆分：`sitemap-core.xml`含全部新增页面、`sitemap-extension-tier1.xml`只含tier1（排除candidate/grace/excluded）
  - Stripe退款：全额立即降级、部分不降级、找不到用户不崩溃，2个场景+2个边界情况全部通过
  - PostHog：page_type在home/pricing/research/dataset正确渲染、checkout_started/signup_completed/purchase_completed三个事件正确触发
  - 全量回归：Explorer筛选、Rankings全部类型、Account三种套餐状态、40个混合状态Extension Detail、Privacy/Terms、Sitemap、robots.txt、健康检查，全部通过，确认没有破坏现有功能

  **✅ 2026-09-30 你在Windows上真实跑通了（266,477个真实插件，2026-09-09快照）**：
  - Candidate: 257,212 / Tier1: 9,265 / Tier1_grace: 0 / Excluded: 0（校验：257,212+9,265=266,477，对得上）
  - 冷启动：9,425个当天已达标，其中9,265个最近7天全部达标直接进tier1，160个从今天开始正常排队
  - Quality Gate淘汰（已过数量门槛、卡在内容质量）：`invalid_description` 95个、`insufficient_history` 9个、`no_growth_signal` 2个，共106个，占比约1.1%——说明能过数量门槛的插件内容基本是完整的
  - `sitemap-extension-tier1.xml`实际URL数量跟9,265对上了；抽查了一个真实tier1插件的Detail页，确认功能正常
  - 这一路上真实MySQL环境暴露了4个bug（见下面明细），全部现场修复验证过，全量同步+SEO计算这条链路现在确认是可靠的

  **2026-09-30 真实MySQL环境（306,306个真实插件）暴露的2个bug，都已修复**：
  1. `seo_tier`字段缺少数据库级默认值——`sync_all_extensions`遇到当天数据里全新的插件（MySQL之前没见过的extension_id）时报`Field 'seo_tier' doesn't have a default value`。原因：Django的`default="candidate"`只是ORM层默认值，不是MySQL schema里真正的`DEFAULT`子句（跟之前`computed_at`/`auto_now`踩过的坑同一类）。修复：给`seo_tier`加了`db_default`，生成新迁移`0011_alter_extension_seo_tier`
  2. `compute_seo_tier`用`_raw_bulk_upsert`只传4个字段（`extension_id`/`seo_tier`/`seo_tier_since`/`seo_qualify_streak_start`）更新已有插件时报`Field 'name' doesn't have a default value`——**这是一个更深层的发现**：MySQL的`INSERT ... ON DUPLICATE KEY UPDATE`语句，在判断"这行到底是插入还是更新"之前，会先按NOT NULL约束校验一遍"候选插入行"凑不凑得齐，哪怕这一批行100%都会命中已有主键走UPDATE分支也不例外。`compute_seo_tier`只想更新4个字段，但Extension的`name`/`slug`等字段是NOT NULL且没有数据库级默认值，于是报错——即使这些行早就存在。**修复方式**：新增`sync_helpers._raw_bulk_update()`（纯UPDATE，不是upsert）专门给这种"只更新已确定存在的行"的场景用，不需要构造完整候选行，天然不受这个限制影响。`compute_seo_tier.py`改用这个新函数。已用真实执行的SQL（不是mock）在sqlite上重新验证过整套状态机11个场景+冷启动3个场景，全部通过
  3. **排查过其它调用`_raw_bulk_upsert`的地方**（`sync_all_extensions.py`两处、`compute_milestones.py`一处）——确认都传了完整字段列表或者被排除的字段本身允许NULL，没有同类问题，不需要改
  4. **`_to_float()`字符串"nan"绕过检测（2026-09-30，Windows真实数据266,477个插件触发，Mac那批306,306个插件没触发）**：原始数据里偶尔会出现字符串`"nan"`这种文本形式的缺失值标记，`pd.isna("nan")`识别不出这是缺失值（只认识真正的NaN/None/NaT），于是继续走`float(value)`——Python的`float("nan")`会"成功"转成一个真正的NaN浮点数，混过检查一路传到MySQL那层，PyMySQL的`escape_float()`才报错拒绝写入（`nan can not be used with MySQL`）。修复：转换后用`result != result`（NaN是唯一"不等于自己"的浮点数）再拦一次。已测试过字符串"nan"/"NaN"/"NAN"、真正的float NaN、None/pd.NA、正常数值这几种情况，确认没有引入回归
  5. **同一个"nan can not be used with MySQL"报错在Windows上修完`_to_float`后仍然复现（同一批次、同一行数据，说明是稳定可复现的），排查`sync_all_extensions.py`确认Extension这批里唯一的浮点字段`rating_value`已经走`_to_float`——逻辑上应该被上面第4点的修复覆盖，但实测没有，说明NaN真正的来源还没找到**（清过`__pycache__`排除了字节码缓存过期的可能性）。没有再花时间逐一排查是哪个具体来源，改成更稳妥的做法：在`_raw_bulk_upsert`/`_raw_bulk_update`真正拼SQL之前，新增`_sanitize_for_mysql()`作为唯一关卡兜底——任何`float`且`!=`自身（NaN的定义性特征）的值，不管从哪个字段、哪种奇怪的原始数据格式漏过来的，一律转成`None`，不用再穷举每一个可能的上游来源。拦截时会打印一行调试信息（`[调试] 拦截到一个NaN值...`），方便下次真的遇到了知道确实被挡住了。已用真实DB写入（不是mock）验证过修复有效
  6. **真正的根因找到了——从一开始就不是`rating_value`，是`description`**：上面第5点的`_sanitize_for_mysql()`补丁上线后，调试打印证实真的拦到了一个NaN，但紧接着换成了新报错`Column 'description' cannot be null`——因为NaN被统一转成了`NULL`，而`description`这一列不允许NULL（应该是空字符串）。顺着这条线索才挖到真正的根因：`raw_csv_reader.py`用`pandas.read_csv(dtype=str)`读原始CSV，**即使指定了`dtype=str`，pandas遇到空单元格依然会给出一个NaN（float类型），不是空字符串**——这是`dtype=str`和pandas缺失值处理各管一段的经典坑。`sync_all_extensions.py`里`descriptions.get(row["id"]) or ""`这句兜底代码因此完全失效：**NaN在Python里是"真值"**（不是0，不算falsy），`nan or ""`会短路直接返回`nan`本身，`""`根本轮不到生效。这才是从最开始那次"nan can not be used with MySQL"报错起就一直存在的真正原因，之前怀疑`rating_value`是误判。**正确的修复**：在`_load_descriptions()`里用`df["description"].fillna("")`在源头把NaN转成空字符串，不依赖下游`or ""`那句不可靠的兜底。已用mock模拟pandas读到空单元格产出NaN的真实场景验证过修复有效，`_sanitize_for_mysql()`兜底逻辑保留（不影响正确性，纯粹是双保险，未来别的字段真出现类似情况还能兜住）
  7. **顺带发现并修复：首页搜索框"搜索插件名称或Extension ID"这句提示文字，实际代码从来没实现过Extension ID搜索**（2026-09-30，你在验证tier1页面时用真实Extension ID搜索触发）——`home()`视图的搜索逻辑一直只有`Extension.objects.filter(name__icontains=query)`，输入一个真实Extension ID只会拿去跟插件名字做字符串匹配，必然搜不到，UI承诺的功能和代码实际做的不一致。修复：搜索条件改成`Q(name__icontains=query) | Q(extension_id__iexact=query)`，ID用精确匹配（`iexact`，不是`icontains`）——ID是精确标识符不需要模糊匹配，而且精确匹配能用上主键索引，在26万+行规模下比对主键做`LIKE '%...%'`全表扫描快得多。顺便把"没有找到匹配的插件"那条提示语里"当前数据库只同步了少量插件用于验证页面，搜索范围有限，不是全量数据"这句**早期阶段留下的过时说法**删掉了——现在数据库有26万+真实插件，这句话已经不准确、会误导用户。已测试过：按名称搜索（不影响原有功能）、按Extension ID精确搜索（新功能，含大小写不敏感）、真正搜不到时的提示文案，全部通过
  8. **顺带发现并修复：Extension Detail页面FAQ区块中英文混杂**（2026-09-30，你截图真实tier1页面"A-B Repeat"时发现——"Is A-B Repeat growing?"这题答案前半句英文，后半句直接接了一整段中文增长摘要）。根因：`_build_faq()`是英文FAQ（题目/答案都设计成英文，比如"How many users does X have?"），但"Is X growing?"和"How has X grown over time?"这两题内部直接复用了`_growth_summary_sentence()`——这个函数是给主页面"用户增长"那段**中文**摘要用的，拼出来的句子（"过去7天增长了 0 名用户（+0.0%）"）直接原样塞进了英文答案里，一句话中英文混杂。修复：新增`_growth_summary_sentence_en(metric)`，跟中文版同样的逻辑（只用非空的7D/30D/90D窗口拼句子，不编造缺失数据）但产出英文（"grew by 500 users over the past 7 days (+3.0%)"这种格式），`_build_faq()`改调用这个英文版本；主页面`"growth_summary"`那处（给中文"用户增长"板块用）保持不变，仍然调用原来的中文版函数。已用sqlite真实渲染测试验证：复现了截图里的零增长场景（7D/30D/90D全部change=0）确认FAQ区块不再混入中文，另外补了一个非零增长场景（7D/30D/90D都有真实增长数字）确认英文句子里的用户数/百分比数字正确，同时确认主页面的中文摘要句子没有受影响，仍然是中文。
  9. **顺带发现并修复：Account页面"联系客服"入口判断条件错误，改成手动在数据库改plan开通的付费账号会永远看不到**（2026-09-30，你在Navicat里手动把测试账号plan改成professional、没走真实Stripe流程时发现"联系客服"那行没显示）。根因：`account.html`里这行用`{% if profile.stripe_customer_id %}`判断要不要显示，只有真的走过Stripe Checkout的账号才有这个字段；以后Custom套餐可能是线下谈的、后台手动开通，不会自动有`stripe_customer_id`，但一样是付费用户，一样需要能找到人工客服入口——用stripe_customer_id判断从一开始就是错的判断依据。修复：改成`{% if profile.plan != "free" %}`，只要不是免费版就显示，不管这个账号是自助Stripe订阅的还是后台手动开通的。顺带按你的要求把"联系客服"这个链接文字后面加上实际邮箱地址（`联系客服（contact@extension-center.com）`），account.html和base.html页脚两处都改了；login.html那处英文"Contact support"没有动，风格不同、没让改。已用sqlite真实渲染测试验证：free套餐不显示、professional+无stripe_customer_id（模拟手动开通场景）现在正确显示、professional+有stripe_customer_id（真实Stripe订阅场景）仍然正常显示无回归，页脚邮箱文字正确显示。

  **下一步（你本机执行，顺序不能变）**：
  1. `python manage.py makemigrations intelligence` + `migrate`（8个新字段：Extension的`description`/`is_unlisted`/`seo_tier`/`seo_tier_since`/`seo_qualify_streak_start`，ExtensionMetric的`user_count_change_90d`/`user_growth_rate_90d`；以及Extension的category/seo_tier + ExtensionMetric的user_count/age_days/user_count_change_7d/user_count_change_30d/user_growth_rate_30d/user_count_growth_acceleration_7d，共8个新索引——对InnoDB是在线DDL不锁表，30万行规模预计几秒到几十秒）
  2. `pip install -r requirements.txt`（新增了`sentry-sdk`）
  3. `python manage.py sync_all_extensions`（全量重新同步，补上`is_unlisted`/`description`/90D增长这几个新字段——**这一步很关键，不跑这个下面compute_seo_tier全部会因为description为空被Quality Gate卡住**）
  4. `python manage.py compute_seo_tier`（第一次跑，会自动触发冷启动逻辑，回算最近7天历史）——**看它最后打印的汇总，把Candidate/Tier1/Excluded计数和Quality Gate淘汰原因分布截图/贴给我**
  5. 抽查几个页面：随便打开一个进了tier1的插件Detail页看robots标签是不是没有noindex、FAQ/Similar Extensions是不是有内容；打开`/category/productivity/`看是否有数据；打开`/sitemap-extension-tier1.xml`数一下URL数量
  6. 如果要用Sentry/PostHog/GSC，去对应平台注册拿到DSN/API Key/验证码，填进`.env`（`.env.example`已经更新了这几行）

  **✅ 2026-09-30 真实环境测试清单进度（你在Windows/Mac上逐项过的，不需要新开外部账号的部分）**：
  - [x] Explorer Stage 5 复查：Featured/Trusted Publisher/By Google三个勾选筛选，用真实数据核对`Extension.objects.filter(...)`的count跟浏览器页面显示的总条数一致，AND组合关系也确认过
  - [x] Account/Stripe 全额退款：`_handle_charge_refunded`（`refunded=True`）立刻把plan降级为free、清空`stripe_subscription_id`，已用真实Django shell调用+DB查询验证（不是mock）
  - [x] Account/Stripe 部分退款：`_handle_charge_refunded`（`refunded=False`）确认不自动降级，plan保持不变
  - [x] Customer Dataset 正式70字段产物：`generate_customer_dataset`跑通，产出CSV真实核对了列数正好70列
  - [x] Sample CSV（Free套餐）：`generate_customer_dataset_sample`跑通，Dataset页面"下载Sample CSV"按钮真实点击下载正常
  - [ ] past_due（Stripe扣款失败宽限期）：你选择先跳过，目前代码行为是"不降级不提示"（见`billing_views.py`里的注释，这是我代为做的默认选择，不是你确认过的产品决策），以后想加"支付遇到问题"提示再单独测
  - [x] Dataset页面"正式历史文件下载"（Professional/Custom套餐）：OSS链路打通后用Mac + 假测试文件（日期2099-01-01，测完已删除）完整走通：上传/`list_oss_dataset_dates()`/签名下载链接/网页Professional账号下载，全部正常
  - [x] 两个本地安全开关：`DJANGO_DEBUG=False`+密钥还是默认值时确认正确拒绝启动（报错信息完整显示"DJANGO_SECRET_KEY, DB_PASSWORD"）；`DJANGO_DEBUG=False`+真实密钥时正常启动；`DJANGO_HTTPS_ENFORCED=True`确认`SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/`SECURE_SSL_REDIRECT`三个都联动变True。（测试过程中发现Windows旧版PowerShell默认控制台编码显示不出中文报错信息，`chcp 65001`切UTF-8后能看到完整内容——这是Windows本地终端编码的问题，不是代码bug，生产环境Linux+gunicorn不会遇到）
  - [x] OSS：新加坡地域（跟生产服务器同地域）开通Bucket（私有ACL+阻止公共访问）+ RAM子账号（自定义策略锁定只能操作这一个Bucket，不是`AliyunOSSFullAccess`）+ AccessKey，全链路测试通过。（过程中修了两个纯环境问题，不是代码bug：①`generate_download_url()`打印出的URL从终端复制到浏览器容易把Python REPL下一行的`>>>`提示符一起带进去、破坏签名产生`SignatureDoesNotMatch`，改用`pbcopy`直接进剪贴板解决；②改了`.env`加OSS凭证后要重启`runserver`进程，不然进程内存里还是读的旧配置（没有OSS凭证），导致`get_oss_bucket()`报错被`dataset()`视图的`except Exception`兜底吞掉，页面显示"下载服务暂时不可用"）
  - [x] Windows历史数据回填：`csv_to_parquet.py`（25字段Analysis Parquet，2023-09-01至2025-09-01）已跑完；70字段Customer Dataset的历史范围回填（`generate_customer_dataset --all`）在Windows上跑着，还没反馈结果，跑完后还要`upload_customer_dataset --all`把历史CSV全部传到OSS（目前Dataset页面能看到的日期还只是Mac这边的测试数据，不是真实历史全量）

## 进行中 / 下一步

- [ ] **CSV处理管线 + 售卖字段/榜单定档**（2026-09-28 扩展到70字段版本，代码已写好，等你本机验证）
  - **Customer Dataset V1 最终定档（2026-09-28 二次确认，55→70字段）**：`snapshot_date` + 25个Core Fields + 15个新增原始字段 + 29个Metrics = **70个字段**
    - 25 Core Fields（跟现有Analysis Parquet同款）：id, name, item_category, user_count, rating_value, rating_count, version, last_update, creation_date, author, author_id, category, payment_type, supported_languages, num_screenshots, num_videos, is_featured, resurrection_date, previous_obsolete_date, is_unlisted, extension_rank, overall_rank, is_trusted_publisher, by_google, email
    - 15 新增原始字段（2026-09-28 从真实 `raw_excel/ranking-stats-*.csv` 表头逐字核对过，拼写100%匹配，不是猜的）：description, website, raw_author_name, publisher_country, url, logo, privacy_policy_url, publisher_address, help_url, size, small_banner, marquee_banner, is_mature, theme_rank, application_rank
    - 29 Metrics：user_count_change_1d/3d/7d/30d/90d/180d(6) + user_growth_rate_1d/7d/30d/90d/180d(5) + user_count_prior_7d_change/growth_acceleration_7d(2) + user_count_prior_30d_change/growth_acceleration_30d(2) + rating_count_change_1d/7d/30d/90d(4) + rating_value_change_30d/90d(2) + extension_rank_change_1d/7d/30d(3) + overall_rank_change_1d/7d/30d(3) + age_days/days_since_update(2)
    - **item_category 范围**：Customer Dataset **包含全部类型**（extension + theme + application），不过滤——网站继续只展示 item_category=="extension"
    - 明确不进入V1的候选字段：emailIsInvalid/versionCode/riskImpact/riskLikelihood/crawlCountry/fetchId/downloadUrl，以及全部 manifest_* 字段（都在原始CSV里，但按业务规则不带入）
    - 数值型字段没有足够历史窗口时必须是NULL，不能用0代替（metrics.py本身已正确处理，照抄即可）
  - **两套Parquet分开、不共用Schema（2026-09-28 架构确认）**：现有 Analysis Parquet（25字段，`scripts/csv_to_parquet.py`产物）继续只服务metrics/events/rankings等内部研究，不因为Customer Dataset的需要而扩展；新增独立的 Customer Dataset Parquet（70字段）作为对外销售数据的标准底稿，Customer Dataset CSV 直接从这份Parquet导出（不是分别拼两次），保证两者Schema物理上一致
  - 三档套餐用同一套70字段Schema，不做"多几列/少几列"的区分——区别只是历史日期范围（Free=样本、Professional=近1年、Custom=完整历史2023至今）+ Custom的定制研究服务
  - **Custom版"2023至今完整历史"目前交付不了**——现有parquet只到2025-09-01起，2023-2025年的原始CSV还没转换。你确认"有全量原始数据，最后再补转换"，生成脚本设计上不受影响（有哪天数据处理哪天）
  - **网站Rankings保持现状**（直接查MySQL简单排序），不改成读取内部 `board_full/*.parquet`
  - Opportunity Signals、Milestones、内部73张榜、Events 都不进入70字段Customer Dataset，继续只服务网站展示/内部研究
  - payment_type 全链路核对过：Analysis Parquet/events.py/MySQL/Market Explorer筛选/Extension Detail/Rankings标签全部保留，内部M22/M40/W20这几张依赖payment_type推断免费付费机会的榜单确认已经删除，不会恢复
  - **脚本**（2026-09-28 从55字段版本重写为70字段版本）：
    - `intelligence/raw_csv_reader.py`（新增）—— 读当天 `raw_excel/ranking-stats-YYYYMMDD.csv`，只取需要的15个新字段（用`usecols`，不整份加载），重命名成snake_case。**70字段版本依赖原始CSV本身，不只是parquet**——以后要补历史某天的Customer Dataset，那天的原始CSV得还在本机
    - `intelligence/management/commands/generate_customer_dataset.py`（重写）—— `metrics.compute_metrics()`拿25核心+29指标 → `raw_csv_reader`读15个新字段 → 按id合并 → 组成70字段 → 先写Parquet（`{CUSTOMER_DATASET_DIR}/parquet/{date}.parquet`）→ 再从这份Parquet读出来写CSV（`{CUSTOMER_DATASET_DIR}/csv/{date}.csv`）
    - 用法不变：`python manage.py generate_customer_dataset`（最新一天）/ `--date 2026-08-01`（指定）/ `--all`（全部日期，历史窗口不够或原始CSV已丢失的日期会被跳过，不中断）
    - ⚠️ **输出目录结构变了**：以前是CSV直接平铺在 `CUSTOMER_DATASET_DIR` 根目录（55字段版本的产物，比如之前跑出来的 `2026-09-01.csv`），现在分成 `parquet/` 和 `csv/` 两个子目录——之前那份55字段的 `2026-09-01.csv` 已经过时，建议删掉重新跑
    - **下一步**：你本机跑一遍 `python manage.py generate_customer_dataset`，确认能正常生成Parquet+CSV各一份、打开CSV看一眼是不是70列、15个新字段的值和NULL是否符合预期，有问题反馈我
  - **上传到阿里云OSS，客户直接从OSS下载**（脚本已写好，等你有空配置阿里云再测试）：
    - 脚本：`intelligence/management/commands/upload_customer_dataset.py`（已同步改成读 `{CUSTOMER_DATASET_DIR}/csv/` 子目录）
    - 用法：`python manage.py upload_customer_dataset --date 2026-09-01`（传一天）/ `--all`（全部）/ 加 `--force` 强制重传
    - **命名规则**：OSS路径 `{OSS_CUSTOMER_DATASET_PREFIX}/{日期}.csv`，默认前缀 `customer-dataset`，跟本地CSV文件名一一对应
    - 用 `oss2.resumable_upload`（分片+断点续传），默认跳过OSS上已有同名同大小的文件，反复跑 `--all` 安全
    - 三档套餐不需要在OSS里分三份存——70字段Schema完全一样，区别只是"发给哪个客户哪段日期范围的下载链接"，这部分权限/链接生成逻辑还没做，是后面单独一块
    - 需要在 `.env` 里配置：`OSS_ACCESS_KEY_ID`/`OSS_ACCESS_KEY_SECRET`/`OSS_ENDPOINT`/`OSS_BUCKET_NAME`/`OSS_CUSTOMER_DATASET_PREFIX`（建议用RAM子账号，别用主账号AccessKey）
    - ⚠️ **建Bucket时权限必须选"私有"（2026-09-28 补充）**：套餐下载权限那套设计（见下面"套餐下载权限逻辑"）的前提是文件只能通过服务端生成的1小时签名链接访问——如果Bucket权限选成了"公共读"，路径规律又是可预测的`customer-dataset/2026-09-01.csv`，不登录也能直接下载全部历史数据，整套权限校验形同虚设
    - **下一步**：你去阿里云建好Bucket（权限选私有）和RAM子账号，配好`.env`，先用单个`--date`测试传一个文件成功，再考虑找时间跑全量

- [ ] **套餐下载权限逻辑（2026-09-28 新增，代码已写好并做过渲染测试，等你本机跑一遍真实数据验证）**
  - **Free套餐 Sample CSV 规则（2026-09-28 确认）**：不能是"某一天全部数据"（哪怕只给1天也等于白送完整市场快照），必须限制行数——固定1个snapshot_date、固定50行、完整70字段Schema（不砍字段，只砍行数）。50行不是简单取前50行，要覆盖不同item_category/user_count规模/payment_type，以及15个新字段的完整度差异（有的产品资料齐全、有的缺，这样客户才能验证NULL处理）
  - **脚本**：`intelligence/management/commands/generate_customer_dataset_sample.py`（新增）—— 读某天已生成的Customer Dataset Parquet，做分层抽样，产物是本地文件 `{CUSTOMER_DATASET_DIR}/sample.csv`（不传OSS，50行几KB直接从Django发送就行）
    - 用法：`python manage.py generate_customer_dataset_sample`（用最新一天）/ `--date 2026-09-01`（指定）
    - 前提：那一天的Customer Dataset Parquet必须已经存在（先跑过 `generate_customer_dataset`）
    - 用固定随机种子，同一天重复生成结果不变；已经用5000行的模拟数据验证过算法：50行不重复、少数item_category类型有保底名额、payment_type全覆盖、15个新字段的完整度有明显分布（不是清一色全有或全没有）
  - **Dataset页面（2026-09-28 确认：是数据产品的主要浏览/下载入口，不是Account页面）**：
    - 旧的"示例CSV下载"（7列，直接查MySQL，不是真实产品）已删除替换——`dataset_sample_csv` 视图现在直接发送 `generate_customer_dataset_sample` 生成的本地 `sample.csv`
    - 页面上的"示例数据预览"表格保留原样（几个核心字段，为了页面可读性，不需要横向展示70列）——跟"Sample CSV下载"是两回事，预览表只是给不下载文件的访客一个直观印象
    - 新增"历史文件下载"区块：Free/未登录看到的是历史文件列表（只读，不能下载）+ 升级CTA；已登录的Professional/Custom看到自己权限范围内每天一个的下载按钮，点击后校验权限再跳转到OSS签名链接（1小时有效），不经过我们自己的服务器代理
  - **新增文件**：`intelligence/customer_dataset_access.py`（`list_oss_dataset_dates()`查OSS现有哪些日期、`get_entitled_dates(plan, dates)`按套餐算权限范围、`generate_download_url()`生成签名链接）；`views.py` 的 `dataset()`/`dataset_sample_csv()` 重写，新增 `dataset_download()` 视图；`urls.py` 加了 `dataset/download/<date>/` 路由
  - **权限范围规则**：Professional=OSS上实际存在日期里最近365个（不是按自然日历，等以后数据补满自然生效）；Custom=OSS上全部日期；Free/未登录=0个正式历史文件（只有Sample CSV）
  - Account页面同步更新：「数据下载权限」文案改成跟这套规则一致（之前写的"专业版自2023年起"是过时占位文案，已修正为"最近约1年"），并加了「前往Dataset页面下载」的链接——Account页面只做说明，不作为主要下载入口
  - **已完成的验证**（这个沙盒环境里做的，不能替代你本机真实数据验证）：`manage.py check` 通过；`dataset.html`/`account.html` 分别用free/professional/custom三种身份实际渲染过，没有模板报错，下载链接能正确生成；抽样算法用模拟数据跑通
  - **下一步**：你本机先跑 `python manage.py generate_customer_dataset_sample` 生成真实的sample.csv，去Dataset页面点"下载Sample CSV"确认能拿到50行70字段的真实文件；再找一个Professional/Custom账号（在Django admin或staff页面把某个测试账号的plan改一下）登录看看"历史文件下载"区块是否正确显示OSS上的日期列表并能下载

- [x] **对照 Claude Design 补齐12项细节（2026-09-28 立项，分5步做，逐步交付验证）**
  - 完整审计结果见2026-09-28对话记录（12项逐条核对Design原稿 `project/Extension Intelligence.dc.html` 和真实代码），这里只记录进度
  - [x] **第1步 · 表格体验**——`theme.css`新增`.ei-table-wrap`通用滚动容器（横向滚动+sticky表头+可见滚动条），`rankings.html`/`explorer.html`表格套上新容器，"插件"列sticky固定；顺带修了Rankings表格外层div写死`overflow:hidden`盖掉滚动行为的小bug。`manage.py check`通过+模板渲染测试过，等你本机浏览器里缩小窗口看效果
  - [x] **第2步 · Rankings补机会信号组**——对照Design的`RANKING_CONFIG`/`RANKING_GROUPS_DEF`：`RANKING_TYPES`(4种)扩展成`RANKING_CONFIG`(8种，3组)，新增1日增长、7日增长率、以及"机会信号"组(新兴增长/增长加速/低关注高增长，复用home()首页完全相同的筛选逻辑，不是新算法)；`rankings.html`改成分组Segmented选择器。机会信号组免费版上限3条(其它组10条)，对照设计稿做法。**已用sqlite假数据端到端测试过全部8种类型+无效type的兜底，真实Django ORM查询跑通，不只是模板渲染测试**。"里程碑速度"组不在这步，见下一步
  - [x] **第3步 · Rankings里程碑速度组**——新批量向量化算法（替代`plugin_history.compute_first_milestones()`逐插件扫描、30万个要跑208天不现实的旧方式）
    - 新命令：`python manage.py compute_milestones`——按天扫描全部parquet（只读id+user_count两列），一次性算出全部插件"首次达到1K/3K/10K/30K/100K用户"是哪天，预计几分钟到十几分钟跑完（不是208天）
    - 结果写进`ExtensionMetric`新增的10个字段（`days_to_1000`/`date_to_1000`...`days_to_100000`/`date_to_100000`）——**没有写`ExtensionChart.milestones`**，是刻意的：那个字段home()首页会把`chart__isnull=False`的全部插件读进Python内存处理，这次也写满30万+行会拖慢首页，跟这次任务无关，所以milestone数据单独存在`ExtensionMetric`（本来就是全部插件已有的表）
    - 语义跟原来`plugin_history.compute_first_milestones()`完全一致（只是算法换了，不是另外发明规则）：以插件在我们数据里第一次出现的那天为起点算"用时"；如果插件一出现用户数就已经超过某个门槛，说明"首次达到"发生在数据范围之前，这一档保持NULL，不会瞎编日期
    - `_raw_bulk_upsert`（之前在`sync_all_extensions.py`里）挪到了`sync_helpers.py`共享，两个命令都在用，避免重复代码
    - Rankings新增"里程碑速度"组（1K/10K/100K三档，对照设计稿），单独一套表格列（#/插件/里程碑/用时/达成日期/当前用户数），升序排序（天数越少排名越靠前）
    - 沙盒里用合成测试数据验证过算法正确性（4种场景，逐项比对手工推算结果一致）；实际跑到真实MySQL时踩到一个bug——`_raw_bulk_upsert`没传`computed_at`字段（这个字段是`auto_now=True`，只在走ORM的`.save()`时才会自动填值，原生SQL批量写绕过了这一层，MySQL报"doesn't have a default value"），已修复（在`METRIC_FIELDS`里显式加上`computed_at`并赋值）
    - **2026-09-29 你本机真实数据验证通过**：跑了`makemigrations`+`migrate`+`compute_milestones`（365天，67秒扫描完，30万+插件写入成功），去Rankings"里程碑速度"组抽查过，结果符合预期。这一步彻底完成
  - **第3步完整完成**
  - [x] **第4步 · Account套餐日期 + 取消订阅**（代码已写好并端到端测试过，等你本机验证）
    - `UserProfile`新增3个字段：`subscription_start_date`/`current_period_end`/`cancel_at_period_end`——`current_period_end`是双重含义的（正常订阅中=下次扣款日期；已经点了取消但还没到期=服务截止日期），靠`cancel_at_period_end`这个标记区分，Account页面文案跟着这个标记变
    - `billing_views.py`：`_handle_checkout_completed`（首次订阅，额外调一次`stripe.Subscription.retrieve()`拿周期日期，Checkout Session本身不带这些）+ `_handle_subscription_change`（每次续费/取消都会跟着更新，`current_period_end`不用额外轮询会自动保持新鲜；真正取消/欠费时清空这3个字段，不留旧数据）
    - 取消订阅走**方案A**：不做显眼按钮，改成一行弱化的"需要取消订阅？联系客服"文字链接，客服邮箱已经填成真实地址 `contact@extension-center.com`；Stripe Billing Portal那边"允许客户自助取消"这个开关要不要关，你自己去Stripe Dashboard的Customer Portal设置里操作，不涉及代码
    - **已端到端测试过完整生命周期**（sqlite+mock，不是真实Stripe）：首次订阅（webhook正确拉取周期日期）→ 用户点取消但未到期（`cancel_at_period_end`正确置真，日期保留，服务应该继续）→ 到期后Stripe发最终取消事件（正确清空日期、降级Free）。三个阶段的数据状态全部符合预期
    - **顺带修了一个日期显示的小bug**：Django模板对`date`对象默认渲染成"Aug. 1, 2026"这种英文格式（因为`LANGUAGE_CODE="en-us"`），跟网站其它地方统一用的`YYYY-MM-DD`格式不一致——订阅日期展示、以及上一步(第3步)Rankings里程碑速度组的"达成日期"列，都补了`|date:"Y-m-d"`显式格式化
    - **2026-09-29 第一次重新设计**：合并三张卡片成单张"订阅摘要"（套餐名+状态文案带日期+管理账单按钮+联系客服链接）
    - **2026-09-29 第二次重新设计**（你指出"管理账单"按钮会让用户自己经Stripe Portal把订阅取消掉，等于绕开了"方案A"想要的人工介入这一环——这个判断是对的，Stripe默认Portal配置本来就允许自助取消，除非你另外去Dashboard手动关掉那个开关，光靠代码这边"不做显眼按钮"防不住"按钮本身就能走到取消页"这条路）：**彻底移除 Billing Portal 入口**，不再依赖"你要记得去Stripe Dashboard关自助取消开关"这个外部前提；订阅状态文案简化成"订阅中"/"已取消自动续费，服务持续到期"/"免费版"三选一，不展示具体日期（`subscription_start_date`/`current_period_end` 这两个字段还留着，数据没丢，只是这次不在页面露出，以后想展示随时能加回来）；新增"套餐包含"区块（复用Pricing页同款功能列表文案，`PLAN_FEATURES`常量），页面不再显得空；右上角按钮改名"查看所有方案"
    - **这次的代价**：付费用户现在完全没有自助更新银行卡/查看历史账单的入口了，任何账单相关的事都要邮件联系客服处理——这是你要的效果（"由我们人工协助…解决问题"），但产品规模变大后如果这类邮件变多，可能需要重新考虑给"更新付款方式"单独开一个入口（不等于开放自助取消）
    - 已用真实Django测试客户端端到端验证过（不只是模板渲染）：免费版/订阅中/已取消待到期三种状态，确认页面上完全找不到 `billing_portal` 这个URL的任何入口
    - **2026-09-29 新增一次性工具**：`python manage.py sync_subscription_dates`——你测试时发现已有的订阅（这个功能上线前就存在的）日期一直显示"—"，原因是webhook只在新订阅/续费/取消这些事件发生时才会写入这两个日期，老订阅不会因为我们加了新字段就被Stripe主动重新推送一次事件。这个命令一次性把现有全部订阅的日期补上，不用等下个月自然续费才有数据。已用mock测试过：只处理真正有`stripe_subscription_id`的账号，正确跳过免费版
    - **下一步**：`python manage.py makemigrations intelligence` + `migrate`（加了3个新字段）→ `python manage.py sync_subscription_dates`（给已有订阅补日期）→ 刷新Account页面确认日期正常显示、布局是不是符合预期
  - [x] **第5步 · Explorer扩展**（代码已写好并端到端测试过，等你本机验证+重新全量同步）
    - `Extension`新增3个字段：`is_featured`/`is_trusted_publisher`/`by_google`（`BooleanField(null=True, blank=True)`）——原始parquet里这三列存的是字符串"True"/"False"，`sync_helpers.py`新增`_to_bool()`转换（大小写不敏感，无法识别/缺失统一转成`None`，不当作`False`处理，"没有这个数据"和"明确是False"是两码事）
    - `sync_all_extensions.py`：`EXTENSION_FIELDS`/`Extension(...)`构造同步补上这三列
    - 筛选UI改成**预设档下拉**（你确认的做法，不是自由Min/Max输入）：`views.py`新增`EXPLORER_NUMERIC_FILTERS`（12档，覆盖用户数/评分/评论数/年龄/Days Since Update/1D·7D·30D增长/7D·30D增长率/Extension Rank/Overall Rank）、`EXPLORER_BOOL_FILTERS`（Featured/Trusted Publisher/By Google三个是/否/全部三态筛选）、`EXPLORER_OPPORTUNITY_OPTIONS`（复用`_opportunity_queryset()`，跟Rankings"机会信号"组、首页机会信号卡片完全同一套业务逻辑，不是第三份实现）
    - `explorer()`视图重写：筛选条件从原来硬编码的if/elif列表改成遍历上述声明式配置动态拼`queryset.filter()`，数值解析包了`try/except ValueError`（手动改URL传非法值不会500，只是这条筛选被忽略）；`active_filters`/`extra_qs`（排序链接/分页链接要带着当前筛选条件）也从硬编码key列表改成通用的`request.GET.copy()`处理，不用每加一个新筛选参数就要同时改好几处
    - `explorer.html`：筛选表单改成对`numeric_filter_defs`/`bool_filter_defs`/`opportunity_options`的循环渲染（不是12个手写的`<select>`块）；分类/商业模式两个动态取值下拉保持原样；结果表格每行插件名旁新增Featured/Trusted/By Google三个标签徽章（复用`modernist.css`已有的`tag-accent`/`tag-neutral`样式）
    - **已用sqlite真实数据端到端测试过**（不只是模板渲染）：60条模拟数据，12个数值预设档逐一筛选验证、3个布尔筛选(是/否)验证、机会标签筛选验证、非法数值参数(`min_users=abc`)不报错验证、无效机会标签值(`bogus_value`)不报错验证、筛选+自定义排序组合验证、排序链接/分页链接正确带着当前全部筛选条件验证（用正则从渲染出的HTML里抓`sort_urls.users`实际href核对）——全部通过
    - **注意：这一步跟前几步不一样，需要重新跑一次全量同步**——`is_featured`/`is_trusted_publisher`/`by_google`这三列现有30万+行都是`NULL`，不会自动补上，必须重新跑`sync_all_extensions`才会覆盖成真实值
    - **下一步（你本机执行，顺序不能变）**：`python manage.py makemigrations intelligence` → `python manage.py migrate` → `python manage.py sync_all_extensions`（全量重新同步，给现有30万+插件补上这三个新字段的真实值，之前几步的migration不需要这一步）→ 去Explorer页面试试新的预设档下拉筛选，勾选Featured/Trusted Publisher/By Google确认能筛出结果、标签徽章正常显示
  - **对照Claude Design补齐12项细节·5步计划全部完成**
  - **本次审计里发现但不在这5步范围内、暂不处理的**：Testimonials（首页新模块，Design里完全没有，需要自己设计）、PostHog埋点、忘记密码、Privacy/Terms/Footer/Contact入口、Sentry——这些不在这次12项范围内，等你需要时再提

- [x] **Dataset页面体验调整 + 全站"1年内/3年以上"措辞统一**（2026-09-29，代码已写好并端到端测试过）
  - **Dataset页面**：历史文件下载列表默认只显示前30个日期，超过30个出现"显示更多"按钮，每点一次展开30个（之前是把套餐权限范围内全部日期一次性渲染进一个固定高度滚动框，不是真正分批）；示例数据预览从20行缩短到5行，表格最右侧新增"⋯"列，悬停提示"还有62个字段未展示"，明确这只是8/70字段的示意
  - **全站措辞统一**（你要求彻查"近1年"，该说"1年内"的说"1年内"，该说"3年以上"的说"3年以上"，不要模糊说法）：
    - `home.html`：首页统计条"近1年"→"1年内"，"25"（数据字段数）→"70"（这两个是真正过时的数字，Customer Dataset从25字段扩展到70字段之后首页从来没同步更新过）——**这里特意保持"1年内"而不是"3年以上"**：首页这组统计是给所有访客看的"当前平台现状"，不是Custom套餐专属承诺，现在parquet历史确实只有约1年，写"3年以上"会是虚报，等历史数据真正回补完成再改
    - `dataset.html`：Professional明确写"1年内历史"，Custom明确写"3年以上完整历史"（原来是"最近约1年"/"全部历史"这种不够精确的说法）
    - `pricing.html`：Professional功能列表加"（1年内历史）"，Custom功能列表加"（3年以上）"，方案对比表格"Chrome Extension 数据集"这一行也标注"1年内 · 完整访问+导出"/"3年以上 · 完整访问+导出"，顺便更新了表格下方已经过时的免责声明（之前写"字段清单和访问范围最终确认后才开放"，现在都已经确认了）
    - `views.py`：Account页面的`download_text`（Professional"可下载1年内的完整历史数据"，Custom"可下载3年以上的完整历史数据"）、`PLAN_FEATURES`套餐包含列表同步加上这两个说法
    - `customer_dataset_access.py`：代码注释里的"近1年"也改成"1年内"，保持代码内部文档和对外说法一致
  - 已用真实Django测试客户端端到端验证：home/pricing/dataset三个页面确认不再出现"近1年"字样、首页70字段数字正确、Account页面Professional/Custom分别显示正确的新措辞

- [ ] **生产部署相关**
  - 买域名、配置DNS
  - 服务器/云主机 + gunicorn+nginx+HTTPS
  - [x] **每日数据自动同步封装（2026-09-29 已写完）**：新命令`daily_sync`，按顺序跑`sync_all_extensions`→`compute_milestones`→`generate_customer_dataset`→`upload_customer_dataset`，某一步失败不影响其它步骤，最后打印汇总、有失败就用非0退出码（供crontab/监控识别）；支持`--skip-sync`/`--skip-milestones`/`--skip-customer-dataset`/`--skip-upload`按需跳过（比如OSS还没配置好先跳过上传）。**不锁定crontab还是Celery beat**——不管以后选哪个调度方式，都只是定时调用`python manage.py daily_sync`这一条命令，等你定了服务器方案，把这一条接进去就行。已用mock测试过：4步正确顺序执行、中间一步失败不阻断后续步骤、全部skip时安全跳过、有失败时正确返回非0退出码
  - 忘记密码/密码重置流程（需要接真实发邮件服务——这个仍然没做，不在这轮范围内）
  - [x] **隐私政策/服务条款页面 + 全站Footer（2026-09-29 已写完；2026-09-30 补充：退款政策已确认写入，PostHog已接入并同步更新了隐私政策措辞）**：新增`/privacy/`/`/terms/`两个页面（`views.py`新增`privacy()`/`terms()`，`urls.py`加路由），内容准确反映代码里真实的数据处理方式（收集邮箱/密码、Google登录信息、Stripe订阅ID、定制研究需求表单内容）。**仍然待你提供真实信息才能替换的【待填写】**：法律实体全称、注册地址、适用法律管辖地——你已确认暂时不提供，先留着占位符，不影响其它功能。另外`base.html`新增了全站Footer（之前完全没有），链接隐私政策/服务条款/联系客服邮箱，顺便解决了"联系方式/客服入口"这一项。已用真实Django测试客户端验证过：两个页面都200 OK、关键内容都在、Footer在首页正确出现且链接可点
  - [x] **安全加固检查（2026-09-29 已写完，比"清单"更进一步——代码里真的会拒绝启动）**：`config/settings.py`新增部署时校验，`DEBUG=False`（生产模式）时如果检测到`DJANGO_SECRET_KEY`或`DB_PASSWORD`还在用代码里写死的开发默认值（`dev-only-not-for-production`/`cei_dev_password`），直接抛`ImproperlyConfigured`拒绝启动——不用再靠"人记得上线前检查清单"，忘配置真实密钥直接进程起不来。`DEBUG=True`（本地开发）完全不受影响。`ALLOWED_HOSTS`本身在生产`DEBUG=False`时配错（比如还是`127.0.0.1`）会导致所有真实域名请求直接400，属于自己会立刻暴露的错误，不需要额外代码校验。已测试过三种情况：本地DEBUG=True正常启动、生产模式还用开发默认值正确拒绝启动（报错信息准确列出具体哪个变量没改）、生产模式配了真实值正常启动
  - [x] **错误监控（2026-09-30代码已接入，等你注册Sentry账号拿DSN）**：`config/settings.py`新增`SENTRY_DSN`（留空不初始化，不会误把沙盒/本地调试报错发到生产项目），配置后自动接入`DjangoIntegration`；`requirements.txt`加了`sentry-sdk`。**下一步**：去sentry.io建一个项目，把DSN填进生产`.env`的`SENTRY_DSN`
  - 数据库备份策略（仍然没做，需要你先定策略）
  - **Stripe 测试模式→生产模式切换（2026-09-28 补充）**：Live模式下Price ID跟测试模式不通用，得在Stripe后台Live模式下重新建一次Professional/Custom两个Price，拿新的price_xxx；Webhook Endpoint也要重新指向真实域名 `https://你的域名/billing/webhook/`，会生成新的whsec_xxx，要替换到生产.env
  - [x] **HTTPS相关Django安全设置（2026-09-29 已写完，等你本机/生产环境验证）**：`config/settings.py`新增`DJANGO_HTTPS_ENFORCED`总开关（默认`False`，不影响本地`http://127.0.0.1`开发），开关打开时同步打开`SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/`SECURE_SSL_REDIRECT`三项，并设置`SECURE_PROXY_SSL_HEADER`（**这项是我额外加的，不在你原始清单里，但没有它`SECURE_SSL_REDIRECT`在gunicorn+nginx架构下会死循环重定向**——nginx终止TLS后转发给gunicorn的是普通http请求，Django自己看不出"这原本是一次https请求"，必须靠nginx转发`X-Forwarded-Proto`请求头+这行配置配套才行，上线时nginx配置别漏了这一步）；`CSRF_TRUSTED_ORIGINS`从新增的`DJANGO_CSRF_TRUSTED_ORIGINS`环境变量读取（Django 5要求带完整scheme，比如`https://your-domain.com`，不能只写域名）。`.env.example`同步加了这两个新变量的说明。已测试过默认值（不影响现有开发环境`manage.py check`通过）和开启后的值（`SESSION_COOKIE_SECURE`等4项正确变`True`，`CSRF_TRUSTED_ORIGINS`正确解析多个域名）——**下一步**：买好域名、nginx配好证书后，生产`.env`里设置`DJANGO_HTTPS_ENFORCED=True`+`DJANGO_CSRF_TRUSTED_ORIGINS=https://你的域名`，同时确认nginx配置里有转发`X-Forwarded-Proto`这一行
  - **原始CSV/raw_excel/两套Parquet的备份（2026-09-28 补充）**：现有"数据库备份策略"只想着MySQL，但200多G原始CSV才是真正不可再生的资产（MySQL/Parquet都是从它算出来的），要不要单独纳入备份计划自己决定
  - **邮件服务缺口（2026-09-28 补充；2026-09-30确认：这轮明确不做）**：忘记密码找回、Custom套餐"定制研究需求表单"提交后的通知，都需要真实邮件服务才能做，你已经明确"当前不做自动邮件系统"——忘记密码这一项改成了登录页"联系客服"人工入口顶替（见下面新增的"首发SEO Index Pool..."条目第5点）；定制研究需求表单目前仍然只存数据库，没有通知，你需要自己定期去`/staff/`或Django admin查看有没有新提交，等以后真的要接邮件服务了这两个缺口一起补
  - [x] **Stripe `past_due` 状态处理（2026-09-29 已写完）**：`billing_views.py`的`_handle_subscription_change`新增`past_due`分支——**这是我代为做的默认选择，不是你确认过的产品决策**：扣款失败进入宽限期时不降级、不清空plan，只刷新`current_period_end`日期，写一条warning日志；理由是Stripe会按自己的重试计划自动重试，成功会收到`active`事件自动恢复，重试耗尽会收到`canceled`/`unpaid`事件，走已有的降级分支，不需要在`past_due`这一步抢先处理。**如果你想要更严格的处理**（比如进入`past_due`就先限制访问、或者Account页面加一条"支付遇到问题"提示），告诉我再加，现在Account页面没有对应这个状态的文案。已用sqlite测试过完整生命周期：active→past_due（plan保留、日期刷新）→重试成功回到active（正常）；以及active→past_due→重试耗尽转canceled（正确降级为免费版）

## 性能优化（已知问题，明确先不动，等上线前统一处理）

全量同步30万+插件之后，Rankings/Explorer/首页这些页面已经感觉到卡顿。已知需要做、但现在先不动的优化：

- [x] **给常用排序/筛选字段加数据库索引（2026-09-30已做，现在就是上线前）**——`ExtensionMetric`的`user_count`/`age_days`/`user_count_change_7d`/`user_count_change_30d`/`user_growth_rate_30d`/`user_count_growth_acceleration_7d`都加了`db_index=True`；顺带原因：这次新增的Category/Best Pages/Similar Extensions都要按`user_count`排序，不加索引这几个新页面在30万+行规模下就是全表扫描排序。跑`migrate`时MySQL加这几个二级索引对InnoDB是在线DDL（不锁表），30万行规模预计几秒到几十秒，不会是长时间锁表操作
- [ ] **每天预生成/缓存热门页面**——比如Rankings默认页、Home页的排行预览，不要每次请求都实时查30万行
- [x] **前端加loading状态/加载动画（2026-09-30已做）**：`base.html`新增全站顶部加载条（纯CSS+无依赖vanilla JS，不需要额外引入任何库）——点击站内链接/提交表单的瞬间就开始显示，排除了新标签页打开/锚点跳转/mailto·tel链接/修饰键点击这几种不会真正离开当前页面的情况，避免误触发；新页面加载完成后旧DOM整体被替换，不需要手动隐藏。已验证不影响任何现有页面渲染

