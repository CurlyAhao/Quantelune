export const SUPPORTED_LOCALES = ["zh-CN", "en"] as const;

export type AppLocale = (typeof SUPPORTED_LOCALES)[number];

export const LANGUAGE_STORAGE_KEY = "statforge.ui-locale";

interface LocaleSources {
  saved?: string | null;
  installer?: string | null;
  browser?: string | null;
}

export function normalizeLocale(
  value: string | null | undefined,
): AppLocale | null {
  if (!value) return null;
  const normalized = value.trim().toLowerCase().replaceAll("_", "-");
  if (
    normalized === "2052" ||
    normalized === "zh" ||
    normalized === "zh-cn" ||
    normalized.startsWith("zh-hans")
  ) {
    return "zh-CN";
  }
  if (
    normalized === "1033" ||
    normalized === "en" ||
    normalized.startsWith("en-")
  ) {
    return "en";
  }
  return null;
}

export function resolvePreferredLocale({
  saved,
  installer,
  browser,
}: LocaleSources): AppLocale {
  return (
    normalizeLocale(saved) ??
    normalizeLocale(installer) ??
    normalizeLocale(browser) ??
    "en"
  );
}

const ZH_CN: Record<string, string> = {
  // Global navigation and account
  Dashboard: "仪表板",
  Projects: "项目",
  Settings: "设置",
  LOCAL: "本地",
  "Toggle color theme": "切换明暗主题",
  "Account menu": "账户菜单",
  Researcher: "研究者",
  "Sign in": "登录",
  "Sign out": "退出登录",
  "Signing out…": "正在退出…",
  "Signed out": "已退出登录",
  "See you at the next analysis.": "期待你的下一次分析。",
  "Get started": "开始使用",
  "Go to Dashboard": "返回仪表板",
  "Back to Projects": "返回项目",
  "Page not found": "页面不存在",
  "The page you are looking for does not exist or has been moved.":
    "你访问的页面不存在或已被移动。",
  "Something went wrong": "出现错误",
  "An unexpected error occurred. Your data is safe — try again or go back to the dashboard.":
    "发生了意外错误。你的数据是安全的，请重试或返回仪表板。",
  Retry: "重试",
  "Try again": "重试",
  Close: "关闭",
  Cancel: "取消",
  Confirm: "确认",
  Delete: "删除",
  Save: "保存",
  Next: "下一步",
  Previous: "上一步",
  Refresh: "刷新",
  Search: "搜索",
  Clear: "清除",
  View: "查看",
  Download: "下载",
  Upload: "上传",
  Actions: "操作",
  Status: "状态",
  Created: "创建时间",
  Updated: "更新时间",
  Description: "描述",
  Name: "名称",
  Title: "标题",
  Optional: "可选",
  "(optional)": "（可选）",
  None: "无",
  "None reported.": "无可报告内容。",
  "Not reported": "未报告",
  Unavailable: "不可用",
  Ready: "就绪",
  Failed: "失败",
  Error: "错误",
  Errors: "错误",
  Warning: "警告",
  Warnings: "警告",
  Passed: "通过",
  Skipped: "已跳过",
  Running: "运行中",
  "Running…": "正在运行…",
  Pending: "等待中",
  Active: "活跃",
  Drafts: "草稿",
  Draft: "草稿",
  DRAFT: "草稿",
  Frozen: "已冻结",
  "Not configured": "未配置",
  Configured: "已配置",
  "No task type": "未指定任务类型",
  rows: "行",
  rev: "修订",

  // Landing, authentication and dashboard
  "Bench overview": "工作台概览",
  "Latest rev": "最新修订",
  "— pick a project or start a new one.": "— 请选择项目或新建项目。",
  "Open-source statistics workbench": "开源统计研究工作台",
  "Quantelune — AI-assisted applied statistics & ML workbench":
    "Quantelune — AI 辅助应用统计与机器学习工作台",
  "Every analysis starts as a project. Create one, drop in a dataset, and Quantelune profiles it for you.":
    "每次分析都从项目开始。创建项目、导入数据，Quantelune 会自动帮助你理解数据结构。",
  "Open the workbench": "打开工作台",
  "Free & open-source — MIT licensed": "自由开源 · MIT 许可证",
  "Your data never leaves your own infrastructure":
    "数据始终保留在你的本地环境",
  "Every method, documented": "每种方法都有说明",
  "Types, null rates and sample values for every column, instantly.":
    "即时查看每个字段的类型、缺失率和样例值。",
  "Workflows, statistics and AI-assisted review — all on the record.":
    "工作流、统计分析和 AI 审查全程留痕。",
  "Sign in to Quantelune": "登录 Quantelune",
  "Restricted bench · credentials required": "受保护工作台 · 需要登录",
  Email: "邮箱",
  Password: "密码",
  "Confirm password": "确认密码",
  "Display name": "显示名称",
  "Signing in…": "正在登录…",
  "Create account": "创建账户",
  "Create an account": "创建账户",
  "Create your account": "创建你的账户",
  "Forging account…": "正在创建账户…",
  "Already registered?": "已经注册？",
  "Sign in instead": "改为登录",
  "New to the workbench?": "第一次使用？",
  "New researcher · bench registration": "新研究者 · 工作台注册",
  "Account created": "账户已创建",
  "Preparing a fresh bench…": "正在准备新的工作台…",
  "Email is required": "请输入邮箱",
  "Enter a valid email address": "请输入有效的邮箱地址",
  "Password is required": "请输入密码",
  "Password must be at least 8 characters": "密码至少需要 8 个字符",
  "Passwords do not match": "两次输入的密码不一致",
  "Passwords hashed server-side, never stored in plain text":
    "密码在服务端加密散列，不会以明文保存",
  "Sessions rotate automatically · cookies are HttpOnly":
    "会话会自动轮换 · Cookie 使用 HttpOnly",
  "Silent token refresh keeps long analyses alive":
    "静默刷新令牌，保障长时间分析不中断",
  "Signed in as": "当前登录",
  "Workspace index": "工作区索引",
  "Every analysis lives inside a project.": "每项分析都归属于一个项目。",
  "All your datasets, one index": "所有数据集，一处管理",
  "Cross-project index": "跨项目索引",
  "Starting points": "起始模板",
  "Start from a proven template": "从经过验证的模板开始",
  "Reference shelf": "方法参考",
  "Reference documentation for each statistical and ML method in the workbench — assumptions, caveats and citations.":
    "查看工作台中统计与机器学习方法的假设、注意事项和引用资料。",

  // Projects
  Project: "项目",
  "New project": "新建项目",
  "Create project": "创建项目",
  "Create your first project": "创建第一个项目",
  "No projects yet": "暂无项目",
  "No projects on the bench yet": "工作台中还没有项目",
  "Create a project to hold your datasets, workflows and results.":
    "创建项目，用于组织数据集、工作流和分析结果。",
  "Project created": "项目已创建",
  "Project deleted": "项目已删除",
  "Delete project": "删除项目",
  "Delete this project?": "删除这个项目？",
  "This cannot be undone.": "此操作无法撤销。",
  "Start a fresh analysis workspace. You can upload data right after.":
    "创建新的分析工作区，随后即可导入数据。",
  "Title is required": "请输入标题",
  "Title must be at most 255 characters": "标题不能超过 255 个字符",
  "Task type": "任务类型",
  "Select a task type": "选择任务类型",
  "Not specified yet": "暂不指定",
  "Statistical analysis": "统计分析",
  "Binary classification": "二分类",
  "Multiclass classification": "多分类",
  Regression: "回归",
  Clustering: "聚类",
  "Anomaly detection": "异常检测",
  "What are you analyzing, and why?": "你要分析什么？研究目的是什么？",
  "What hypothesis will this project test?": "该项目要检验什么假设？",
  "Research question": "研究问题",
  "About this project": "关于本项目",
  "No description yet — open the project to add one.":
    "尚未填写描述，可以打开项目后补充。",
  "No description yet — the story of this analysis is still unwritten.":
    "尚未填写描述，这项分析的背景仍待补充。",
  "Next steps": "下一步",
  Revision: "修订版本",
  Rev: "修订",
  "Every change in Quantelune is versioned. The revision counter on this project increments as analysis artifacts are committed.":
    "Quantelune 会记录每次变更；提交分析产物时，项目修订号会递增。",
  "Loading project…": "正在加载项目…",
  "Loading columns…": "正在加载字段…",
  "Project section error:": "项目页面错误：",
  "Section error": "页面错误",
  "This section encountered an error. Other parts of Quantelune are still working normally.":
    "该页面发生错误，Quantelune 的其他功能仍可正常使用。",

  // Project tabs
  Overview: "概览",
  Data: "数据",
  Profiling: "数据画像",
  Quality: "数据质量",
  Workflow: "工作流",
  Statistics: "统计分析",
  ML: "机器学习",
  Evaluation: "模型评估",
  "AI Review": "AI 审查",
  Agent: "智能代理",
  Report: "报告",
  Versions: "版本",
  "Project sections": "项目功能",

  // Data and upload
  Dataset: "数据集",
  Datasets: "数据集",
  "All datasets": "全部数据集",
  "No datasets yet": "暂无数据集",
  "No data on the bench yet — upload a CSV, Excel or Parquet file to begin.":
    "工作台中还没有数据，请上传 CSV、Excel 或 Parquet 文件。",
  "Drop in a CSV, Excel workbook or Parquet file — Quantelune infers the schema and profiles every column.":
    "导入 CSV、Excel 或 Parquet 文件，Quantelune 将推断结构并分析每个字段。",
  "Upload a dataset": "上传数据集",
  "Upload data": "上传数据",
  "Drag & drop your dataset": "拖放数据文件",
  "Drop it — the forge is hot": "松开即可上传",
  "or browse files from your computer": "或从电脑中选择文件",
  "browse files": "浏览文件",
  "up to 100 MB": "最大 100 MB",
  "Excel sheet name (optional)": "Excel 工作表名称（可选）",
  "Defaults to the first sheet": "默认使用第一张工作表",
  "Choose a dataset file": "选择数据文件",
  "Choose a different file": "选择其他文件",
  "Cancel upload": "取消上传",
  "Transferring to storage…": "正在传输到本地存储…",
  "Processing…": "正在处理…",
  "Upload complete": "上传完成",
  "Upload another": "继续上传",
  "Preview dataset": "预览数据集",
  "Retry upload": "重试上传",
  "File rejected": "文件被拒绝",
  "Duplicate file detected": "检测到重复文件",
  "Unsupported file type. Upload a CSV, Excel (.xlsx), or Parquet file.":
    "不支持该文件类型，请上传 CSV、Excel（.xlsx）或 Parquet 文件。",
  "Dataset deleted": "数据集已删除",
  "Delete dataset": "删除数据集",
  "Delete this dataset?": "删除这个数据集？",
  "Fetching dataset…": "正在读取数据集…",
  "Data preview": "数据预览",
  "Column schema": "字段结构",
  "No rows to preview.": "没有可预览的数据行。",
  "Types, null rates and sample values": "字段类型、缺失率与样例值",
  Rows: "行数",
  Columns: "列数",
  Cols: "列",
  File: "文件",
  "File size": "文件大小",
  Format: "格式",
  "Safe filename": "安全文件名",
  "SHA-256": "SHA-256",
  "Physical type": "物理类型",
  "Polars type": "Polars 类型",
  Nullable: "允许为空",
  "Not nullable": "不允许为空",
  Nulls: "缺失数",
  Unique: "唯一值",
  "Sample values": "样例值",

  // Profiling
  Profile: "画像",
  "Run profiling": "运行数据画像",
  "Computing profile…": "正在计算数据画像…",
  "Profiling columns…": "正在分析字段…",
  "Loading profiling…": "正在加载数据画像…",
  "No profile yet": "尚无画像结果",
  "Run a profile to compute per-column statistics, missing-value analysis, correlations, and a data-quality score.":
    "运行数据画像，计算字段统计、缺失情况、相关性和数据质量分数。",
  "Upload a dataset first, then run a profile to explore its structure and quality.":
    "请先上传数据，再运行画像以了解数据结构和质量。",
  "Profiling failed": "数据画像失败",
  "The profile could not be computed.": "无法完成数据画像计算。",
  "Empty profile": "画像结果为空",
  "This profile has no result payload. Try recomputing.":
    "该画像没有结果内容，请重新计算。",
  "The underlying data changed since this profile was computed. Recompute to refresh the statistics.":
    "数据在画像生成后发生了变化，请重新计算以更新统计结果。",
  Recompute: "重新计算",
  "Complete rows": "完整行",
  "All-null columns": "全空字段",
  "High-missing columns (>50%)": "高缺失字段（>50%）",
  Missing: "缺失",
  Correlations: "相关性",
  "Correlation method": "相关方法",
  "No numeric columns available for correlation.":
    "没有可用于相关分析的数值字段。",
  "Grey cells indicate pairs with insufficient overlapping non-null values. Blue = negative correlation, red = positive correlation.":
    "灰色表示有效重叠样本不足；蓝色表示负相关，红色表示正相关。",
  Count: "数量",
  Mean: "均值",
  Median: "中位数",
  "Std dev": "标准差",
  Min: "最小值",
  Max: "最大值",
  Skewness: "偏度",
  Outliers: "异常值",
  "Top values": "高频值",
  "Avg length": "平均长度",
  Earliest: "最早时间",
  Latest: "最晚时间",
  "Span (days)": "跨度（天）",
  Duplicates: "重复数",
  "Duplicate rows": "重复行",
  "Duplicate row rate": "重复行比例",
  Completeness: "完整性",
  Distribution: "分布",

  // Quality
  "Data quality": "数据质量",
  "Data quality score": "数据质量分数",
  "Run quality check": "运行质量检查",
  "Running quality checks…": "正在运行质量检查…",
  "Quality checks in progress…": "质量检查进行中…",
  "Loading quality…": "正在加载数据质量…",
  "No quality check yet": "尚未运行质量检查",
  "Upload a dataset first, then run a quality check to score its completeness, validity, uniqueness, and more.":
    "请先上传数据，再运行质量检查，评估完整性、有效性、唯一性等维度。",
  "Quality run failed": "质量检查失败",
  "The quality engine could not complete this run.":
    "质量引擎未能完成本次运行。",
  "Retry run": "重试运行",
  "Back to latest": "返回最新结果",
  Findings: "问题项",
  History: "历史",
  "No findings recorded for this run": "本次运行没有问题项",
  "No findings match the current filters": "没有符合当前筛选条件的问题项",
  "No quality runs yet": "尚无质量检查历史",
  "All severities": "全部严重程度",
  "All categories": "全部类别",
  "All statuses": "全部状态",
  Severity: "严重程度",
  Category: "类别",
  "Column name…": "字段名称…",
  "Search title or summary…": "搜索标题或摘要…",
  "Clear all filters": "清除全部筛选",
  "Clear filters": "清除筛选",
  "What this rule checks": "该规则检查什么",
  "Why it triggered": "为什么触发",
  Observed: "观察值",
  Threshold: "阈值",
  "Affected rows": "受影响行数",
  "Affected ratio": "受影响比例",
  "Affected columns": "受影响字段",
  "How to fix it": "如何修复",
  Limitations: "限制",
  Evidence: "证据",
  "Total rules": "规则总数",
  "Rule outcomes": "规则结果",
  "Score deductions": "扣分项",
  "No deductions — every rule stayed within its thresholds.":
    "没有扣分项，所有规则均在阈值范围内。",
  Validity: "有效性",
  Uniqueness: "唯一性",
  Consistency: "一致性",
  Temporal: "时间一致性",
  Usability: "可用性",
  "Advanced settings": "高级设置",
  "Enable OHLCV / crypto rules": "启用 OHLCV / 加密资产规则",
  "Run with these settings": "使用这些设置运行",

  // Statistics and regression
  "Configure analysis": "配置分析",
  "Run analysis": "运行分析",
  "Computing statistics…": "正在计算统计结果…",
  "Statistical analysis in progress…": "统计分析进行中…",
  "Loading statistics…": "正在加载统计分析…",
  "No statistical run yet": "尚无统计分析结果",
  "No statistical runs yet for this dataset.": "该数据集尚无统计分析历史。",
  "Choose a dataset above to configure and run a statistical analysis.":
    "请先选择数据集，再配置并运行统计分析。",
  "No methods available.": "没有可用的统计方法。",
  "Descriptive statistics": "描述统计",
  "Alternative hypothesis": "备择假设",
  "Two-sided (≠)": "双侧（≠）",
  "Greater (>)": "大于（>）",
  "Less (<)": "小于（<）",
  "Confidence level": "置信水平",
  "Missing-value policy": "缺失值策略",
  "Listwise deletion": "整行删除",
  "Pairwise deletion": "成对删除",
  "Error on missing": "存在缺失时报错",
  Variable: "变量",
  Variables: "变量",
  "Target variable": "目标变量",
  "Target (outcome) variable": "目标（结果）变量",
  Predictors: "预测变量",
  "Numeric predictors": "数值预测变量",
  "Categorical predictors": "分类预测变量",
  "Reference value (μ₀)": "参考值（μ₀）",
  "Regularization α": "正则化 α",
  "Effect size": "效应量",
  "Confidence intervals": "置信区间",
  "Assumption & applicability checks": "假设与适用性检查",
  Statistic: "统计量",
  "Test statistic": "检验统计量",
  "p-value": "p 值",
  "Statistically significant at α = 0.05": "在 α = 0.05 水平上具有统计显著性",
  "Not statistically significant at α = 0.05":
    "在 α = 0.05 水平上不具有统计显著性",
  "This method is not applicable to the selected variables.":
    "该方法不适用于所选变量。",
  "No confidence interval reported for this method.": "该方法未报告置信区间。",
  "No effect size for this method.": "该方法未报告效应量。",
  "No descriptive statistics.": "没有描述统计结果。",
  "No result recorded for this run.": "本次运行没有保存结果。",
  "Analysis failed": "分析失败",
  "The statistical engine could not complete this run.":
    "统计引擎未能完成本次运行。",
  "Coefficient table": "系数表",
  Coefficient: "系数",
  Coefficients: "系数",
  "Std. error": "标准误",
  "Odds ratio": "优势比",
  "Categorical encoding": "分类编码",
  "Collinearity (VIF)": "多重共线性（VIF）",
  "Residuals vs fitted": "残差与拟合值",
  "Normal Q-Q": "正态 Q-Q 图",
  Converged: "已收敛",

  // Machine learning
  "Machine learning": "机器学习",
  "Configure experiment": "配置实验",
  "Train & evaluate": "训练并评估",
  "Training and evaluating models…": "正在训练并评估模型…",
  "Loading machine learning…": "正在加载机器学习…",
  "Choose a dataset above to configure and run a machine-learning experiment.":
    "请先选择数据集，再配置并运行机器学习实验。",
  "Upload a dataset first, then build a reproducible ML experiment with guarded splits, leakage checks and multi-metric comparison.":
    "请先上传数据，再创建包含安全划分、泄漏检查和多指标比较的可复现机器学习实验。",
  "Select a target, at least one feature, and at least one model.":
    "请选择目标变量、至少一个特征和至少一个模型。",
  "Select a target…": "选择目标变量…",
  "Select a target column…": "选择目标字段…",
  "Numeric features": "数值特征",
  "Categorical features": "分类特征",
  "Random seed": "随机种子",
  Folds: "折数",
  "Cross-validation": "交叉验证",
  "Data-leakage checks": "数据泄漏检查",
  "Potential data leakage detected:": "检测到潜在数据泄漏：",
  "Feature importance": "特征重要性",
  "Confusion matrix": "混淆矩阵",
  "No completed models to compare.": "没有可比较的已完成模型。",
  "No importance available for this model.": "该模型没有可用的特征重要性。",
  "The experiment could not complete.": "实验未能完成。",
  "ML run failed": "机器学习运行失败",
  "Download pipeline artifact": "下载流水线产物",
  "Continuous targets": "连续目标",
  "Two-class prediction": "二分类预测",
  "Three or more classes": "三个或更多类别",
  "Assess overfit, class balance and metric tradeoffs.":
    "评估过拟合、类别平衡和指标权衡。",

  // Workflow
  "Analysis workflow": "分析工作流",
  "Build an analysis workflow": "创建分析工作流",
  "Node palette": "节点面板",
  "Select a dataset": "选择数据集",
  "Select a dataset…": "选择数据集…",
  "Select a dataset to run the workflow.": "选择用于运行工作流的数据集。",
  "Upload a dataset first so the workflow has data to run against.":
    "请先上传数据，工作流才能运行。",
  "Click a node to edit its parameters, or add nodes from the palette and connect them left→right.":
    "点击节点编辑参数，或从面板添加节点并从左向右连接。",
  "No parameters.": "该节点没有参数。",
  "Delete node": "删除节点",
  Validate: "验证",
  Validated: "已验证",
  "Graph is valid.": "工作流图验证通过。",
  "Transformations, statistics, ML": "数据转换、统计与机器学习",
  Run: "运行",
  "Select Columns": "选择字段",
  "Filter Rows": "筛选行",
  Sample: "抽样",
  CLEANING: "数据清洗",
  "Handle Missing": "处理缺失值",
  "Convert Type": "类型转换",
  "Drop Duplicates": "删除重复行",
  "Outlier Strategy": "异常值策略",
  "Descriptive Stats": "描述统计",
  Correlation: "相关分析",
  "Group Comparison": "组间比较",
  "Chi-Square": "卡方检验",
  ANOVA: "方差分析",
  "MACHINE LEARNING": "机器学习",
  Split: "数据划分",
  Preprocessing: "预处理",
  "Feature Selection": "特征选择",
  Model: "模型",
  "Cross-Validation": "交叉验证",
  Interpretation: "解释",
  OUTPUT: "输出",
  Table: "表格",
  Chart: "图表",
  Notebook: "笔记本",
  Export: "导出",
  Inspector: "节点检查器",

  // AI and Agent
  "AI Assistant": "AI 助手",
  "AI provider": "AI 服务商",
  "AI is not configured. Set": "AI 尚未配置，请设置",
  "AI is not configured. Set AI_ENABLED=true, a provider, model, base URL and API key in the backend .env file. All other features work normally.":
    "AI 尚未配置。请在后端 .env 文件中设置 AI_ENABLED=true、服务商、模型、Base URL 和 API Key。其他功能仍可正常使用。",
  ", a provider, model, base URL and API key in the backend":
    "、服务商、模型、Base URL 和 API Key，并配置后端",
  "file. All other features work normally.": "文件。其他功能仍可正常使用。",
  "Not configured · deterministic tools remain available":
    "未配置 · 确定性分析工具仍可使用",
  "no model": "未选择模型",
  "Checking AI status…": "正在检查 AI 状态…",
  "Method advisor": "方法建议",
  "Interpret a statistical result (p-value, CI, effect size).":
    "解释统计结果（p 值、置信区间、效应量）。",
  "ML evaluation review": "机器学习评估审查",
  "Leakage review": "泄漏审查",
  "Quality review": "质量审查",
  "Result explanation": "结果解释",
  "Regression review": "回归审查",
  "Explain data-quality findings in plain language.":
    "用易懂的语言解释数据质量问题。",
  "Suggest statistical methods for your research question.":
    "根据研究问题建议统计方法。",
  "Review diagnostic plots and coefficient estimates.":
    "审查诊断图和系数估计。",
  "Review data-leakage checks for a model run.": "审查模型运行的数据泄漏检查。",
  Generate: "生成",
  "Loading Agent…": "正在加载智能代理…",
  "Loading tools…": "正在加载工具…",
  "Available tools:": "可用工具：",
  "I'm your Quantelune analysis assistant. Describe what you'd like to analyze, and I'll plan and execute the steps. I can inspect data, run quality checks, compute statistics, train models, and generate reports — all on your local machine.":
    "我是 Quantelune 分析助手。请描述你想分析的内容，我会规划并执行步骤。我可以检查数据、运行质量检查、计算统计结果、训练模型并生成报告，所有操作都在本机完成。",
  "Planning…": "正在规划…",
  "Executing…": "正在执行…",
  "Execution failed. Check the plan details.": "执行失败，请检查计划详情。",
  "Describe what you want to analyze… e.g. 'Check data quality, run descriptive stats on revenue, and create a bar chart'":
    "描述你希望完成的分析，例如：“检查数据质量，对收入做描述统计，并创建柱状图”",
  Pause: "暂停",
  Resume: "继续",

  // Report, versions and placeholders
  "Model Evaluation": "模型评估",
  "Report Builder": "报告生成器",
  "Module pending": "模块待完成",
  "Compose exportable reports that cite the exact dataset and workflow revisions they were generated from.":
    "生成可导出的报告，并准确引用对应的数据集和工作流版本。",
  "Metrics, calibration curves and error analysis side by side for every candidate model in the project.":
    "并排比较项目中候选模型的指标、校准曲线和误差分析。",
  "Version status": "版本状态",
  Versioning: "版本管理",
  "Freeze project": "冻结项目",
  "Project frozen.": "项目已冻结。",
  Fork: "派生副本",
  "Fork title": "副本标题",
  "Create an editable copy of this frozen project.":
    "从该冻结项目创建一个可编辑副本。",
  "Download .zip": "下载 ZIP",
  "Export ZIP": "导出 ZIP",
  "Reproducible research package with code, reports, and metadata.":
    "包含代码、报告和元数据的可复现研究包。",
  "Draft — editable project, runs are append-only":
    "草稿 — 项目可编辑，运行记录只追加",
  "Freeze — snapshot fingerprints + artifacts (Export page)":
    "冻结 — 保存数据指纹和产物（导出页面）",
  "Fork — editable copy of a frozen version": "派生 — 冻结版本的可编辑副本",
  "· Draft — editable project, runs are append-only":
    "· 草稿 — 项目可编辑，运行记录只追加",
  "· Freeze — snapshot fingerprints + artifacts (Export page)":
    "· 冻结 — 保存数据指纹和产物（导出页面）",
  "· Fork — editable copy of a frozen version": "· 派生 — 冻结版本的可编辑副本",

  // Project data and empty states
  "Manage data": "管理数据",
  "CSV · XLSX · Parquet, up to 100 MB": "CSV · XLSX · Parquet，最大 100 MB",
  "Inspect the schema profile": "检查字段结构画像",
  SIZE: "大小",
  UPLOADED: "上传时间",
  "Choose a dataset above to inspect its profile, or run a new one.":
    "请在上方选择数据集以查看画像或运行新的画像。",
  "Choose a dataset above to review its latest quality run, or start a new one.":
    "请在上方选择数据集以查看最新质量结果或开始新的检查。",

  // Settings and diagnostics
  "Local runtime": "本地运行环境",
  "System diagnostics": "系统诊断",
  "Paths and readiness checks are shown here; credentials and the per-launch session token are never displayed.":
    "这里显示路径和就绪检查；凭据和每次启动生成的会话令牌不会被展示。",
  Runtime: "运行模式",
  Database: "数据库",
  "Dataset storage": "数据集存储",
  "Application data": "应用数据",
  "Desktop sidecar": "桌面 Sidecar",
  "Web API": "Web API",
  "Sidecar authentication active": "Sidecar 身份验证已启用",
  "Browser session authentication": "浏览器会话身份验证",
  "Running local checks…": "正在执行本地检查…",
  "Diagnostics unavailable.": "系统诊断不可用。",
  Language: "界面语言",
  "Interface language": "界面语言",
  "Simplified Chinese": "简体中文",
  English: "英语",
  "Use the installer choice or select a language for this device.":
    "默认使用安装器选择，也可以为此设备手动切换语言。",
};

const ENGLISH_MONTHS: Record<string, number> = {
  Jan: 1,
  Feb: 2,
  Mar: 3,
  Apr: 4,
  May: 5,
  Jun: 6,
  Jul: 7,
  Aug: 8,
  Sep: 9,
  Oct: 10,
  Nov: 11,
  Dec: 12,
};

function renderChineseTimestamp(
  monthName: string,
  day: string,
  year: string,
  hour: string,
  minute: string,
  meridiem: string,
): string {
  const month = ENGLISH_MONTHS[monthName] ?? monthName;
  const hour12 = Number(hour);
  const hour24 =
    meridiem.toUpperCase() === "PM"
      ? hour12 === 12
        ? 12
        : hour12 + 12
      : hour12 === 12
        ? 0
        : hour12;
  return `${year}年${month}月${Number(day)}日 ${String(hour24).padStart(2, "0")}:${minute}`;
}

const ZH_PATTERNS: Array<[RegExp, (...matches: string[]) => string]> = [
  [/^Loading (.+)…$/i, (value) => `正在加载${value}…`],
  [/^Preview (.+)$/i, (value) => `预览 ${value}`],
  [/^Download (.+)$/i, (value) => `下载 ${value}`],
  [/^Delete (.+)$/i, (value) => `删除 ${value}`],
  [/^Page (\d+) of (\d+)$/i, (page, total) => `第 ${page} / ${total} 页`],
  [/^(\d+) rows?$/i, (count) => `${count} 行`],
  [/^(\d+) columns?$/i, (count) => `${count} 列`],
  [/^(\d+) projects? on the bench$/i, (count) => `工作台中有 ${count} 个项目`],
  [
    /^(\d+) files? · upload CSV, Excel or Parquet up to 100 MB$/i,
    (count) => `${count} 个文件 · 可上传最大 100 MB 的 CSV、Excel 或 Parquet`,
  ],
  [/^\+(\d+) more$/i, (count) => `另有 ${count} 个`],
  [/^rev (\d+)$/i, (revision) => `修订 ${revision}`],
  [
    /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4}), (\d{2}):(\d{2}) (AM|PM)$/i,
    renderChineseTimestamp,
  ],
  [
    /^Module pending · ships week (\d+)$/i,
    (week) => `模块待完成 · 计划第 ${week} 周交付`,
  ],
  [
    /^Viewing a historical run from (.+)$/i,
    (date) => `正在查看 ${date} 的历史运行`,
  ],
  [
    /^File is (.+) — the limit is 100 MB\.$/i,
    (size) => `文件大小为 ${size}，上限为 100 MB。`,
  ],
  [
    /^Execution (.+): (\d+) completed, (\d+) failed\.$/i,
    (status, completed, failed) =>
      `执行状态 ${status}：完成 ${completed} 步，失败 ${failed} 步。`,
  ],
  [/^Error: (.+)$/i, (message) => `错误：${message}`],
];

export function translateUiText(text: string, locale: AppLocale): string {
  if (locale === "en" || !text) return text;
  const leading = text.match(/^\s*/)?.[0] ?? "";
  const trailing = text.match(/\s*$/)?.[0] ?? "";
  const value = text.trim();
  if (!value) return text;
  const exact = ZH_CN[value];
  if (exact) return `${leading}${exact}${trailing}`;
  for (const [pattern, render] of ZH_PATTERNS) {
    const match = value.match(pattern);
    if (match) return `${leading}${render(...match.slice(1))}${trailing}`;
  }
  return text;
}
