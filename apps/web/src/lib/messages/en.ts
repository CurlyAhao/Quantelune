import { coreEn } from "./core-en";
import { researchEn } from "./research-en";

/** Semantic product messages from the fixed A03 input corpus. */
export const messagesEn = {
  ...coreEn,
  ...researchEn,
  "ui.ai.aProviderModelBaseUrlAndApiKeyInTheBackend":
    ", a provider, model, base URL and API key in the backend",
  "ui.ai.aiAssistant": "AI Assistant",
  "ui.ai.aiIsNotConfiguredSet": "AI is not configured. Set",
  "ui.ai.checkingAiStatus": "Checking AI status…",
  "ui.ai.fileAllOtherFeaturesWorkNormally":
    "file. All other features work normally.",
  "ui.ai.generate": "Generate",
  "ui.ai.leakageReview": "Leakage review",
  "ui.ai.loadingAiReview": "Loading AI Review…",
  "ui.ai.methodAdvisor": "Method advisor",
  "ui.ai.mlEvaluationReview": "ML evaluation review",
  "ui.ai.ms": "ms",
  "ui.ai.qualityReview": "Quality review",
  "ui.ai.regressionReview": "Regression review",
  "ui.ai.resultExplanation": "Result explanation",
  "ui.ai.tokens": "Tokens:",
  "ui.auth.accountCreated": "Account created",
  "ui.auth.aiContextLeavesOnlyAfterYourExplicitApprovalRawRowsStayLocalByDefault":
    "AI context leaves only after your explicit approval; raw rows stay local by default",
  "ui.auth.alreadyRegistered": "Already registered?",
  "ui.auth.checkingSession": "Checking session…",
  "ui.auth.confirmPassword": "Confirm password",
  "ui.auth.createAnAccount": "Create an account",
  "ui.auth.createYourAccount": "Create your account",
  "ui.auth.displayName": "Display name",
  "ui.auth.email": "Email",
  "ui.auth.everyAnalysis": "Every analysis",
  "ui.auth.freeOpenSourceMitLicensed": "Free & open-source — MIT licensed",
  "ui.auth.httponlyCookieSessionsNoTokensInLocalstorage":
    "HttpOnly cookie sessions — no tokens in localStorage",
  "ui.auth.leftOff": "left off",
  "ui.auth.min8Chars": "Min. 8 chars",
  "ui.auth.newResearcherBenchRegistration":
    "New researcher · bench registration",
  "ui.auth.newToTheWorkbench": "New to the workbench?",
  "ui.auth.onTheRecord": "on the record",
  "ui.auth.oneAccount": "One account.",
  "ui.auth.password": "Password",
  "ui.auth.passwordsDoNotMatch": "Passwords do not match",
  "ui.auth.passwordsHashedServerSideNeverStoredInPlainText":
    "Passwords hashed server-side, never stored in plain text",
  "ui.auth.pickUpWhere": "Pick up where",
  "ui.auth.projectsDatasetsAndRevisionsAreTiedToYourIdentitySoEveryResultYouPublishCanBeTracedBackToItsSource":
    "Projects, datasets and revisions are tied to your identity, so every result you publish can be traced back to its source.",
  "ui.auth.repeatIt": "Repeat it",
  "ui.auth.restrictedBenchCredentialsRequired":
    "Restricted bench · credentials required",
  "ui.auth.sessionsRotateAutomaticallyCookiesAreHttponly":
    "Sessions rotate automatically · cookies are HttpOnly",
  "ui.auth.signIn": "Sign in",
  "ui.auth.signInInstead": "Sign in instead",
  "ui.auth.silentTokenRefreshKeepsLongAnalysesAlive":
    "Silent token refresh keeps long analyses alive",
  "ui.auth.stat": "Stat",
  "ui.auth.statforgeKeepsEveryUploadProfileAndModelRunOnTheRecordSignInToReturnToYourProjects":
    "Quantelune keeps every upload, profile and model run on the record — sign in to return to your projects.",
  "ui.auth.uploadCsvXlsxParquetUpTo100Mb":
    "Upload CSV · XLSX · Parquet up to 100 MB",
  "ui.auth.welcomeBackValue": "Welcome back, {value0}",
  "ui.auth.welcomeToTheBenchValue": "Welcome to the bench, {value0}.",
  "ui.auth.yourData": "your data",
  "ui.charts.loadingHeatmap": "Loading heatmap…",
  "ui.charts.noNumericColumnsAvailableForCorrelation":
    "No numeric columns available for correlation.",
  "ui.charts.valueMissingValue": "{value0}<br/>missing: <b>{value1}%</b>",
  "ui.charts.valueValueRValue": "{value0} × {value1}<br/>r = <b>{value2}</b>",
  "ui.common.aThresholdKeyIsUnknownOrItsValueIsOutOfRange":
    "A threshold key is unknown or its value is out of range.",
  "ui.common.accessDenied": "Access denied",
  "ui.common.accountMenu": "Account menu",
  "ui.common.agent": "Agent",
  "ui.common.aiIsNotConfiguredSetAiEnabledTrueAProviderModelBaseUrlAndApiKeyInTheBackendEnvFileAllOtherFeaturesWorkNormally":
    "AI is not configured. Set AI_ENABLED=true, a provider, model, base URL and API key in the backend .env file. All other features work normally.",
  "ui.common.aiReview": "AI Review",
  "ui.common.anAccountWithThisEmailAlreadyExistsTrySigningIn":
    "An account with this email already exists. Try signing in.",
  "ui.common.anOpenSourceVisualAiAssistedReproducibleResearchWorkbenchForAppliedStatisticsAndMachineLearning":
    "An open-source, visual, AI-assisted, reproducible research workbench for applied statistics and machine learning",
  "ui.common.anUnexpectedErrorOccurredYourDataIsSafeTryAgainOrGoBackToTheDashboard":
    "An unexpected error occurred. Your data is safe — try again or go back to the dashboard.",
  "ui.common.analysisWorkflow": "Analysis workflow",
  "ui.common.analyzeReview": "Analyze & review",
  "ui.common.archived": "Archived",
  "ui.common.assessOverfitClassBalanceAndMetricTradeoffs":
    "Assess overfit, class balance and metric tradeoffs.",
  "ui.common.assumptionApplicabilityChecks":
    "Assumption & applicability checks",
  "ui.common.availableTools": "Available tools:",
  "ui.common.browseFiles": "browse files",
  "ui.common.browserSessionAuthentication": "Browser session authentication",
  "ui.common.cancelUpload": "Cancel upload",
  "ui.common.cannotReachTheStatforgeApiIsTheBackendRunning":
    "Cannot reach the Quantelune API. Is the backend running?",
  "ui.common.checkingActualMaterialCitations":
    "Checking actual material citations…",
  "ui.common.chooseADatasetFile": "Choose a dataset file",
  "ui.common.chooseADifferentFile": "Choose a different file",
  "ui.common.cleaning": "CLEANING",
  "ui.common.clickANodeToEditItsParametersOrAddNodesFromThePaletteAndConnectThemLeftRight":
    "Click a node to edit its parameters, or add nodes from the palette and connect them left→right.",
  "ui.common.close": "Close",
  "ui.common.codePoints": "; code points [",
  "ui.common.coefficient": "Coefficient",
  "ui.common.coefficients": "Coefficients",
  "ui.common.cols": "Cols",
  "ui.common.cols.uploadDropzone": "cols ·",
  "ui.common.completedWithErrors": "Completed with errors",
  "ui.common.composeExportableReportsThatCiteTheExactDatasetAndWorkflowRevisionsTheyWereGeneratedFrom":
    "Compose exportable reports that cite the exact dataset and workflow revisions they were generated from.",
  "ui.common.confidenceIntervals": "Confidence intervals",
  "ui.common.configured": "Configured",
  "ui.common.confirm": "Confirm",
  "ui.common.confusionMatrix": "Confusion matrix",
  "ui.common.connectionProblem": "Connection problem",
  "ui.common.consistency": "Consistency",
  "ui.common.continueInWorkspace": "Continue in workspace",
  "ui.common.continuousTargets": "Continuous targets",
  "ui.common.createAccount": "Create account",
  "ui.common.createProject": "Create project",
  "ui.common.critical": "Critical",
  "ui.common.crossValidation": "Cross-validation",
  "ui.common.csvExcelXlsxOrParquetUpTo100MbTheSchemaIsProfiledAutomaticallyAfterUpload":
    "CSV, Excel (.xlsx) or Parquet, up to 100 MB. The schema is profiled automatically after upload.",
  "ui.common.csvXlsxParquet": "CSV · XLSX · PARQUET",
  "ui.common.csvXlsxParquetUpTo100Mb": "CSV · XLSX · Parquet, up to 100 MB",
  "ui.common.data": "Data",
  "ui.common.dataLeakageChecks": "Data-leakage checks",
  "ui.common.datasetNotFound": "Dataset not found",
  "ui.common.datasetNotReady": "Dataset not ready",
  "ui.common.datasetsAreLimitedTo100Mb": "Datasets are limited to 100 MB.",
  "ui.common.defaultsToTheFirstSheet": "Defaults to the first sheet",
  "ui.common.deleteDataset": "Delete dataset",
  "ui.common.deleteProject": "Delete project",
  "ui.common.deleted": "Deleted",
  "ui.common.describeWhatYouWantToAnalyzeEGCheckDataQualityRunDescriptiveStatsOnRevenueAndCreateABarChart":
    "Describe what you want to analyze… e.g. 'Check data quality, run descriptive stats on revenue, and create a bar chart'",
  "ui.common.descriptiveStatistics": "Descriptive statistics",
  "ui.common.desktopSidecar": "Desktop sidecar",
  "ui.common.diagnosticsUnavailable": "Diagnostics unavailable.",
  "ui.common.downloadPipelineArtifact": "Download pipeline artifact",
  "ui.common.downloadUnavailableCheckCurrentAccessSourceAndThe":
    "Download unavailable. Check current access, source, and the",
  "ui.common.downloadZip": "Download .zip",
  "ui.common.draft": "Draft",
  "ui.common.draft.alternate": "DRAFT",
  "ui.common.draftEditableProjectRunsAreAppendOnly":
    "Draft — editable project, runs are append-only",
  "ui.common.dragDropYourDataset": "Drag & drop your dataset",
  "ui.common.dropItTheForgeIsHot": "Drop it — the forge is hot",
  "ui.common.duplicateFile": "Duplicate file",
  "ui.common.duplicateFileDetected": "Duplicate file detected",
  "ui.common.emailAlreadyRegistered": "Email already registered",
  "ui.common.emailIsRequired": "Email is required",
  "ui.common.enterAValidEmailAddress": "Enter a valid email address",
  "ui.common.error": "Error",
  "ui.common.everyAnalysisLivesInsideAProject":
    "Every analysis lives inside a project.",
  "ui.common.everyMethodDocumented": "Every method, documented",
  "ui.common.excelSheetNameOptional": "Excel sheet name (optional)",
  "ui.common.executing": "Executing…",
  "ui.common.executionFailedCheckThePlanDetails":
    "Execution failed. Check the plan details.",
  "ui.common.explainDataQualityFindingsInPlainLanguage":
    "Explain data-quality findings in plain language.",
  "ui.common.featureImportance": "Feature importance",
  "ui.common.file": "File",
  "ui.common.fileIsValueTheLimitIs100Mb":
    "File is {value0} — the limit is 100 MB.",
  "ui.common.fileRejected": "File rejected",
  "ui.common.fileTooLarge": "File too large",
  "ui.common.findings": "findings",
  "ui.common.forgeRawData": "Forge raw data",
  "ui.common.forgingAccount": "Forging account…",
  "ui.common.forkEditableCopyOfAFrozenVersion":
    "Fork — editable copy of a frozen version",
  "ui.common.freezeProject": "Freeze project",
  "ui.common.freezeSnapshotFingerprintsArtifactsExportPage":
    "Freeze — snapshot fingerprints + artifacts (Export page)",
  "ui.common.fromYourComputer": "from your computer",
  "ui.common.getStarted": "Get started",
  "ui.common.goToDashboard": "Go to Dashboard",
  "ui.common.graphIsValid": "Graph is valid.",
  "ui.common.greater": "Greater (>)",
  "ui.common.high": "High",
  "ui.common.howStatforgeWorks": "How Quantelune works",
  "ui.common.iMYourStatforgeAnalysisAssistantDescribeWhatYouDLikeToAnalyzeAndILlPlanAndExecuteTheStepsICanInspectDataRunQualityChecksComputeStatisticsTrainModelsAndGenerateReportsAllOnYourLocalMachine":
    "I'm your Quantelune analysis assistant. Describe what you'd like to analyze, and I'll plan and execute the steps. I can inspect data, run quality checks, compute statistics, train models, and generate reports — all on your local machine.",
  "ui.common.importManageData": "Import / manage data",
  "ui.common.incorrectEmailOrPassword": "Incorrect email or password.",
  "ui.common.info": "Info",
  "ui.common.inspector": "Inspector",
  "ui.common.interpretAStatisticalResultPValueCiEffectSize":
    "Interpret a statistical result (p-value, CI, effect size).",
  "ui.common.into": "into",
  "ui.common.invalidThreshold": "Invalid threshold",
  "ui.common.irisCsvDatasetPreview": "iris.csv — dataset preview",
  "ui.common.itMayHaveBeenDeleted": "It may have been deleted.",
  "ui.common.label150Rows5ColsSha2569f2cAb41":
    "· 150 rows × 5 cols · sha256 9f2c…ab41",
  "ui.common.language": "Language",
  "ui.common.less": "Less (<)",
  "ui.common.loading": "Loading",
  "ui.common.loadingMethods": "Loading methods…",
  "ui.common.loadingTemplates": "Loading templates…",
  "ui.common.loadingTools": "Loading tools…",
  "ui.common.loadingValue": "Loading {value0}…",
  "ui.common.local": "LOCAL",
  "ui.common.locateOriginalMaterialTextOrAManualPosition":
    "Locate original material text or a manual position",
  "ui.common.low": "Low",
  "ui.common.machineLearning": "Machine learning",
  "ui.common.machineLearning.alternate": "MACHINE LEARNING",
  "ui.common.manualRegion": "Manual region:",
  "ui.common.manualTimePosition": "Manual time position:",
  "ui.common.manuallyConfirmedSourceNoOcrTranscriptionOrMediaUnderstanding":
    "Manually confirmed source; no OCR, transcription or media understanding.",
  "ui.common.materialSha": "Material SHA:",
  "ui.common.materialSourcesApprovedForThisRequest":
    "Material sources approved for this request",
  "ui.common.materials": "Materials",
  "ui.common.materialsAndCitations": "Materials and citations",
  "ui.common.medium": "Medium",
  "ui.common.methodLibrary": "Method Library",
  "ui.common.methodsAndProjectTools": "Methods and project tools",
  "ui.common.mibLimitRemoteStorageRedirectsAreUnavailableInThisPreview":
    "MiB limit; remote storage redirects are unavailable in this preview.",
  "ui.common.missingValuePolicy": "Missing-value policy",
  "ui.common.mitLicensed": "MIT licensed",
  "ui.common.ml": "ML",
  "ui.common.modulePending": "Module pending",
  "ui.common.modulePendingShips": "Module pending · ships",
  "ui.common.name": "Name",
  "ui.common.noCompletedModelsToCompare": "No completed models to compare.",
  "ui.common.noConfidenceIntervalReportedForThisMethod":
    "No confidence interval reported for this method.",
  "ui.common.noDescriptionYetOpenTheProjectToAddOne":
    "No description yet — open the project to add one.",
  "ui.common.noDescriptionYetTheStoryOfThisAnalysisIsStillUnwritten":
    "No description yet — the story of this analysis is still unwritten.",
  "ui.common.noDescriptiveStatistics": "No descriptive statistics.",
  "ui.common.noEffectSizeForThisMethod": "No effect size for this method.",
  "ui.common.noFindingsMatchTheCurrentFilters":
    "No findings match the current filters",
  "ui.common.noFindingsRecordedForThisRun": "No findings recorded for this run",
  "ui.common.noImportanceAvailableForThisModel":
    "No importance available for this model.",
  "ui.common.noModel": "no model",
  "ui.common.noParameters": "No parameters.",
  "ui.common.noQualityRunYet": "No quality run yet",
  "ui.common.noSavedObjectsYet": "No saved objects yet",
  "ui.common.noTaskType": "No task type",
  "ui.common.none": "None",
  "ui.common.noneReported": "None reported.",
  "ui.common.notApplicable": "Not applicable",
  "ui.common.notConfigured": "Not configured",
  "ui.common.notConfiguredDeterministicToolsRemainAvailable":
    "Not configured · deterministic tools remain available",
  "ui.common.notNullable": "Not nullable",
  "ui.common.notReported": "Not reported",
  "ui.common.notSignedIn": "Not signed in",
  "ui.common.notStatisticallySignificantAtAlpha005":
    "Not statistically significant at α = 0.05",
  "ui.common.nullable": "Nullable",
  "ui.common.numericPredictors": "Numeric predictors",
  "ui.common.oddsRatio": "Odds ratio",
  "ui.common.openSourceStatisticsWorkbench": "Open-source statistics workbench",
  "ui.common.openTheWorkbench": "Open the workbench",
  "ui.common.optional": "Optional",
  "ui.common.or": "or",
  "ui.common.orBrowseFilesFromYourComputer":
    "or browse files from your computer",
  "ui.common.output": "OUTPUT",
  "ui.common.page": "Page ",
  "ui.common.pageNotFound": "Page not found",
  "ui.common.passwordIsRequired": "Password is required",
  "ui.common.passwordMustBeAtLeast8Characters":
    "Password must be at least 8 characters",
  "ui.common.passwordTooWeak": "Password too weak",
  "ui.common.pause": "Pause",
  "ui.common.pending": "Pending",
  "ui.common.planning": "Planning…",
  "ui.common.pleaseSignInAgain": "Please sign in again.",
  "ui.common.pleaseSignInToContinue": "Please sign in to continue.",
  "ui.common.polarsPoweredProfiling": "polars-powered profiling",
  "ui.common.polarsType": "Polars type",
  "ui.common.potentialDataLeakageDetected": "Potential data leakage detected:",
  "ui.common.preWiredProjectsForCommonStudiesABTestsCohortAnalysesBenchmarkSuitesInsteadOfABlankBench":
    "Pre-wired projects for common studies — A/B tests, cohort analyses, benchmark suites — instead of a blank bench.",
  "ui.common.previewDataset": "Preview dataset",
  "ui.common.primary": "Primary",
  "ui.common.processing": "Processing…",
  "ui.common.profile": "Profile",
  "ui.common.profiled": "profiled",
  "ui.common.profiling": "Profiling",
  "ui.common.projectNotFound": "Project not found",
  "ui.common.projectObjectsAndTools": "Project objects and tools",
  "ui.common.projectSectionError": "Project section error:",
  "ui.common.projectSections": "Project sections",
  "ui.common.rSquared": "R² =",
  "ui.common.ready": "ready",
  "ui.common.ready.format": "Ready",
  "ui.common.ready.page": "READY",
  "ui.common.referenceDocumentationForEachStatisticalAndMlMethodInTheWorkbenchAssumptionsCaveatsAndCitations":
    "Reference documentation for each statistical and ML method in the workbench — assumptions, caveats and citations.",
  "ui.common.referenceShelf": "Reference shelf",
  "ui.common.reportBuilder": "Report Builder",
  "ui.common.reproducibleResearchPackageWithCodeReportsAndMetadata":
    "Reproducible research package with code, reports, and metadata.",
  "ui.common.requestFailedValue": "Request failed ({value0})",
  "ui.common.researcher": "Researcher",
  "ui.common.resume": "Resume",
  "ui.common.retryRun": "Retry run",
  "ui.common.retryUpload": "Retry upload",
  "ui.common.rev": "rev",
  "ui.common.reviewDataLeakageChecksForAModelRun":
    "Review data-leakage checks for a model run.",
  "ui.common.reviewDiagnosticPlotsAndCoefficientEstimates":
    "Review diagnostic plots and coefficient estimates.",
  "ui.common.rows": "rows ×",
  "ui.common.run": "Run",
  "ui.common.runAQualityCheckToInspectThisDataset":
    "Run a quality check to inspect this dataset.",
  "ui.common.running": "Running",
  "ui.common.running.alternate": "Running…",
  "ui.common.saveStatforgeSingleAnalysisOutput":
    "Save Quantelune single-analysis output",
  "ui.common.search": "Search",
  "ui.common.seconds": " seconds;",
  "ui.common.seeYouAtTheNextAnalysis": "See you at the next analysis.",
  "ui.common.selectADataset": "Select a dataset…",
  "ui.common.selectADatasetToRunTheWorkflow":
    "Select a dataset to run the workflow.",
  "ui.common.sepalLengthDistribution": "sepal_length distribution",
  "ui.common.sessionExpired": "Session expired",
  "ui.common.settings": "Settings",
  "ui.common.sidecarAuthenticationActive": "Sidecar authentication active",
  "ui.common.signInFailed": "Sign-in failed",
  "ui.common.signInToStatforge": "Sign in to Quantelune",
  "ui.common.signOut": "Sign out",
  "ui.common.signedOut": "Signed out",
  "ui.common.signingIn": "Signing in…",
  "ui.common.signingOut": "Signing out…",
  "ui.common.simplifiedChinese": "Simplified Chinese",
  "ui.common.size": "SIZE",
  "ui.common.source": "Source ·",
  "ui.common.startAFreshAnalysisWorkspaceYouCanUploadDataRightAfter":
    "Start a fresh analysis workspace. You can upload data right after.",
  "ui.common.startFromAProvenTemplate": "Start from a proven template",
  "ui.common.startingPoints": "Starting points",
  "ui.common.statforgeAiAssistedAppliedStatisticsMlWorkbench":
    "Quantelune — AI-assisted applied statistics & ML workbench",
  "ui.common.statforgeAiAssistedStatisticsMlWorkbench":
    "Quantelune — AI-Assisted Statistics & ML Workbench",
  "ui.common.statforgeIsAReproducibleBenchForAppliedStatisticsAndMachineLearningUploadADatasetGetAFullSchemaProfileRunAnalysesAndKeepEveryRevisionOnTheRecord":
    "Quantelune is a reproducible bench for applied statistics and machine learning — upload a dataset, get a full schema profile, run analyses, and keep every revision on the record.",
  "ui.common.statisticallySignificantAtAlpha005":
    "Statistically significant at α = 0.05",
  "ui.common.statistics": "Statistics",
  "ui.common.storageError": "Storage error",
  "ui.common.suggestStatisticalMethodsForYourResearchQuestion":
    "Suggest statistical methods for your research question.",
  "ui.common.templates": "Templates",
  "ui.common.temporal": "Temporal",
  "ui.common.testStatistic": "Test statistic",
  "ui.common.theDatasetMustFinishProcessingBeforeItCanBeChecked":
    "The dataset must finish processing before it can be checked.",
  "ui.common.theExperimentCouldNotComplete":
    "The experiment could not complete.",
  "ui.common.theFileCouldNotBeStoredPleaseTryAgain":
    "The file could not be stored. Please try again.",
  "ui.common.theFileWasWrittenToTheSelectedLocationOpenItIndependentlyToVerifyItsContents":
    "The file was written to the selected location. Open it independently to verify its contents.",
  "ui.common.theMaterialCitationIsUnavailableOrItsVersionHasChanged":
    "The material citation is unavailable or its version has changed.",
  "ui.common.thePageYouAreLookingForDoesNotExistOrHasBeenMoved":
    "The page you are looking for does not exist or has been moved.",
  "ui.common.thePipeline": "The pipeline",
  "ui.common.theProfileCouldNotBeComputed":
    "The profile could not be computed.",
  "ui.common.theQualityEngineCouldNotCompleteThisRun":
    "The quality engine could not complete this run.",
  "ui.common.theStatisticalEngineCouldNotCompleteThisRun":
    "The statistical engine could not complete this run.",
  "ui.common.thisCannotBeUndone": "This cannot be undone.",
  "ui.common.thisExactFileHasAlreadyBeenUploadedToThisProject":
    "This exact file has already been uploaded to this project.",
  "ui.common.thisMethodIsNotApplicableToTheSelectedVariables":
    "This method is not applicable to the selected variables.",
  "ui.common.threeOrMoreClasses": "Three or more classes",
  "ui.common.titleIsRequired": "Title is required",
  "ui.common.titleMustBeAtMost255Characters":
    "Title must be at most 255 characters",
  "ui.common.toggleColorTheme": "Toggle color theme",
  "ui.common.trainEvaluate": "Train & evaluate",
  "ui.common.training": "Training",
  "ui.common.transferringToStorage": "Transferring to storage…",
  "ui.common.transformationsStatisticsMl": "Transformations, statistics, ML",
  "ui.common.tryAgain": "Try again",
  "ui.common.twoClassPrediction": "Two-class prediction",
  "ui.common.typesNullRatesAndSampleValues":
    "Types, null rates and sample values",
  "ui.common.typesNullRatesAndSampleValuesForEveryColumnInstantly":
    "Types, null rates and sample values for every column, instantly.",
  "ui.common.uniqueness": "Uniqueness",
  "ui.common.unsupportedFileType": "Unsupported file type",
  "ui.common.unsupportedFileTypeUploadACsvExcelXlsxOrParquetFile":
    "Unsupported file type. Upload a CSV, Excel (.xlsx), or Parquet file.",
  "ui.common.upTo100Mb": "up to 100 MB",
  "ui.common.updatedValue": "Updated {value0}",
  "ui.common.upload": "Upload",
  "ui.common.uploadACsvExcelXlsxOrParquetFile":
    "Upload a CSV, Excel (.xlsx), or Parquet file.",
  "ui.common.uploadADatasetDragAndDropAFileHereOrPressEnterToBrowse":
    "Upload a dataset: drag and drop a file here, or press Enter to browse",
  "ui.common.uploadADatasetFirstSoTheWorkflowHasDataToRunAgainst":
    "Upload a dataset first so the workflow has data to run against.",
  "ui.common.uploadAnother": "Upload another",
  "ui.common.uploadComplete": "Upload complete",
  "ui.common.uploadData": "Upload data",
  "ui.common.uploadFailedValue": "Upload failed ({value0})",
  "ui.common.uploadStatus": "upload_status:",
  "ui.common.uploaded": "Uploaded",
  "ui.common.uploaded.alternate": "UPLOADED",
  "ui.common.uploading": "Uploading",
  "ui.common.usability": "Usability",
  "ui.common.validating": "Validating",
  "ui.common.validity": "Validity",
  "ui.common.versions": "Versions",
  "ui.common.warning": "Warning",
  "ui.common.webApi": "Web API",
  "ui.common.week2BuildAuthDataLayer": "Week 2 build · auth + data layer",
  "ui.common.whatAreYouAnalyzingAndWhy": "What are you analyzing, and why?",
  "ui.common.workflow": "Workflow",
  "ui.common.workflowsStatisticsAndAiAssistedReviewAllOnTheRecord":
    "Workflows, statistics and AI-assisted review — all on the record.",
  "ui.common.youDoNotHaveAccessToThisProject":
    "You do not have access to this project.",
  "ui.common.yourDataNeverLeavesYourOwnInfrastructure":
    "Your data never leaves your own infrastructure",
  "ui.dashboard.active": "Active",
  "ui.dashboard.benchOverview": "Bench overview",
  "ui.dashboard.createYourFirstProject": "Create your first project",
  "ui.dashboard.dashboard": "Dashboard",
  "ui.dashboard.drafts": "Drafts",
  "ui.dashboard.everyAnalysisStartsAsAProjectCreateOneDropInADatasetAndStatforgeProfilesItForYou":
    "Every analysis starts as a project. Create one, drop in a dataset, and Quantelune profiles it for you.",
  "ui.dashboard.latestRev": "Latest rev",
  "ui.dashboard.loadingDashboard": "Loading dashboard…",
  "ui.dashboard.newProject": "New project",
  "ui.dashboard.noProjectsOnTheBenchYet": "No projects on the bench yet",
  "ui.dashboard.pickAProjectOrStartANewOne":
    "— pick a project or start a new one.",
  "ui.dashboard.projects": "Projects",
  "ui.dashboard.signedInAs": "Signed in as",
  "ui.data.aSearchableCrossProjectViewOfEveryDatasetYouHaveUploadedSizesSchemasAndReuseCountsIncluded":
    "A searchable, cross-project view of every dataset you have uploaded — sizes, schemas and reuse counts included.",
  "ui.data.aiContextForTheCurrentFile": "AI context for the current file",
  "ui.data.allDatasets": "All datasets",
  "ui.data.datasetIdentityUnavailable":
    "The dataset source identity is missing. Return to the project and select a dataset again.",
  "ui.data.allYourDatasetsOneIndex": "All your datasets, one index",
  "ui.data.attention": "attention",
  "ui.data.breadcrumb": "Breadcrumb",
  "ui.data.cols": "cols",
  "ui.data.columnSchema": "Column schema",
  "ui.data.crossProjectIndex": "Cross-project index",
  "ui.data.currentFile": "Current file",
  "ui.data.dataPreview": "Data preview",
  "ui.data.datasetDeleted": "Dataset deleted",
  "ui.data.datasets": "Datasets",
  "ui.data.delete": "Delete",
  "ui.data.deleteThisDataset": "Delete this dataset?",
  "ui.data.download": "Download",
  "ui.data.downloadValue": "Download {value0}",
  "ui.data.dropInACsvExcelWorkbookOrParquetFileStatforgeInfersTheSchemaAndProfilesEveryColumn":
    "Drop in a CSV, Excel workbook or Parquet file — Quantelune infers the schema and profiles every column.",
  "ui.data.fetchingDataset": "Fetching dataset…",
  "ui.data.highNullRate20DetectedIn": "High null rate (>20%) detected in:",
  "ui.data.loadingDataset": "Loading dataset…",
  "ui.data.loadingDatasets": "Loading datasets…",
  "ui.data.mimeType": "MIME type",
  "ui.data.noDatasetsYet": "No datasets yet",
  "ui.data.noRowsToPreview": "No rows to preview.",
  "ui.data.noSamples": "no samples",
  "ui.data.null": "null",
  "ui.data.nulls": "nulls",
  "ui.data.of": "of",
  "ui.data.previewValue": "Preview {value0}",
  "ui.data.processingFailed": "Processing failed",
  "ui.data.profilingColumns": "Profiling columns…",
  "ui.data.reviewThemInTheSchemaTab": ". Review them in the schema tab.",
  "ui.data.rows": "rows",
  "ui.data.safeFilename": "Safe filename",
  "ui.data.selectFile": "Select file",
  "ui.data.sha256": "SHA-256",
  "ui.data.sha256.dataClient": "sha256",
  "ui.data.showing": "Showing",
  "ui.data.stillProcessing": "Still processing",
  "ui.data.thePreviewAppearsAsSoonAsValidationCompletesRefreshInAMoment":
    ". The preview appears as soon as validation completes — refresh in a moment.",
  "ui.data.thisDatasetIs": "This dataset is",
  "ui.data.uploaded": "uploaded",
  "ui.data.validated": "Validated",
  "ui.data.valueValueUploadCsvExcelOrParquetUpTo100Mb":
    "{value0} {value1} · upload CSV, Excel or Parquet up to 100 MB",
  "ui.data.willBePermanentlyRemovedFromThisProject":
    ") will be permanently removed from this project.",
  "ui.data.willBePermanentlyRemovedFromThisProjectYouWillReturnToTheDatasetList":
    ") will be permanently removed from this project. You will return to the dataset list.",
  "ui.materials.aSelectedCitationVersionIsUnavailableClearAndReviewTheSelectionsAgain":
    "A selected citation version is unavailable; clear and review the selections again.",
  "ui.materials.audioMaterial": "Audio material",
  "ui.materials.candidateTextOnTheCurrentPage":
    "Candidate text on the current page",
  "ui.materials.citation": "Citation ·",
  "ui.materials.citationExcerpt": "Citation excerpt",
  "ui.materials.clearAndRefreshMaterialCitations":
    "Clear and refresh material citations",
  "ui.materials.createCitation": "Create citation",
  "ui.materials.currentMaterial": "Current material",
  "ui.materials.downloadOriginalPdf": "Download original PDF",
  "ui.materials.forThisPageSAiContextAwaitingEgressApproval":
    "For this page's AI context, awaiting egress approval",
  "ui.materials.iConfirmThatIReviewedThisCitationOrManualPositionSeparateFromEgressApproval":
    "I confirm that I reviewed this citation or manual position (separate from egress approval)",
  "ui.materials.ifThisBrowserDoesNotDisplayTheOriginalPdfDownloadAndViewItLocallyTheTextBelowIsAPerPageExtractionCandidateThatStillRequiresReview":
    "If this browser does not display the original PDF, download and view it locally; the text below is a per-page extraction candidate that still requires review.",
  "ui.materials.inspectConfirmAndLocateMaterials":
    "Inspect, confirm and locate materials",
  "ui.materials.jumpToStart": "Jump to start",
  "ui.materials.loadingMaterials": "Loading materials…",
  "ui.materials.localPlaybackDuration": "Local playback duration:",
  "ui.materials.manualLocationRegion": "Manual location region",
  "ui.materials.manualNote": "Manual note",
  "ui.materials.materialValue": "Material {value0}",
  "ui.materials.materialsAndVerifiableSources":
    "Materials and verifiable sources",
  "ui.materials.ocrTranscriptionAndNumericExtractionAreUnavailable":
    ". OCR, transcription and numeric extraction are unavailable.",
  "ui.materials.offsetsAreZeroBasedUnicodeCodePointsInAHalfOpenStartEndRangeSelectTextOrEnterAnExactRangeEachExcerptIsLimitedTo2000CodePoints":
    "Offsets are zero-based Unicode code points in a half-open [start, end) range; select text or enter an exact range. Each excerpt is limited to 2000 code points.",
  "ui.materials.onStatisticsMlFilePagesSelectTheSameConfirmedCitationFromTheRightPanelEarlierApprovalsDoNotCarryAcrossPages":
    "On statistics/ML/file pages, select the same confirmed citation from the right panel; earlier approvals do not carry across pages.",
  "ui.materials.pageValueValueValue": "Page {value0} {value1}–{value2}",
  "ui.materials.playMaterial": "Play material",
  "ui.materials.regionsUseNormalized01CoordinatesTheBorderRepresentsAManualPositionNotVisualRecognition":
    "Regions use normalized 0–1 coordinates; the border represents a manual position, not visual recognition.",
  "ui.materials.saveCitationAndReviewStatus": "Save citation and review status",
  "ui.materials.savedCitation": "Saved citation",
  "ui.materials.savedCitationR": "Saved citation r",
  "ui.materials.secondsNotAResearchMeasurement":
    " seconds (not a research measurement).",
  "ui.materials.selectMaterial": "Select material",
  "ui.materials.selectMaterialCitationsForThisPageUpTo5ShortExcerpts":
    "Select material citations for this page (up to 5 short excerpts)",
  "ui.materials.selectOnlyManuallyConfirmedSourcesApproveEgressAnewEachTurnAndReviewAgainWhenTheSourceOrVersionChanges":
    "Select only manually confirmed sources; approve egress anew each turn and review again when the source or version changes.",
  "ui.materials.textExtractionUnavailable": "Text extraction unavailable:",
  "ui.materials.textPageNumber": "Text page number",
  "ui.materials.theManualPositionExceedsTheCurrentPlaybackBoundsNoJumpWasMade":
    "The manual position exceeds the current playback bounds; no jump was made.",
  "ui.materials.theOriginalTableStaysOnTheDataPageMaterialsDoNotProduceMeasurementColumnsExtractedTextIsACandidateImagesAudioAndVideoSupportOnlyViewingPlaybackAndManualPositionNotes":
    "The original table stays on the data page. Materials do not produce measurement columns; extracted text is a candidate. Images, audio and video support only viewing, playback and manual position notes.",
  "ui.materials.uploadMaterial": "Upload material",
  "ui.materials.videoMaterial": "Video material",
  "ui.materials.viewOriginalBytesNoOcrTranscriptionOrMediaUnderstandingVideoTimePositionsAreManualCandidatesBrowserDurationIsUsedOnlyForLocalPlaybackBounds":
    "View original bytes; no OCR, transcription or media understanding. Video time positions are manual candidates; browser duration is used only for local playback bounds.",
  "ui.materials.viewOriginalPdf": "View original PDF",
  "ui.materials.wavDurationIsCheckedAgainstTheServerSideFileHeaderMp4PositionsAreManualCandidatesPlaybackJumpsMustWaitForBrowserMetadataAndVerifyBounds":
    "WAV duration is checked against the server-side file header; MP4 positions are manual candidates. Playback jumps must wait for browser metadata and verify bounds.",
  "ui.ml.accuracy": "Accuracy",
  "ui.ml.actual": "Actual",
  "ui.ml.areaUnderTheRocCurveRequiringSavedPredictionScores":
    "Area under the ROC curve, requiring saved prediction scores",
  "ui.ml.balancedAccuracy": "Balanced accuracy",
  "ui.ml.baselineUnderTheSameProtocol": "Baseline under the same protocol",
  "ui.ml.cancellationRequestedCheckHistoryForThisRunSFinalStatus":
    "Cancellation requested; check history for this run's final status.",
  "ui.ml.categoricalFeatures": "Categorical features",
  "ui.ml.checksWereNotReturnedThisDoesNotEstablishTheAbsenceOfDataLeakage":
    "Checks were not returned; this does not establish the absence of data leakage.",
  "ui.ml.chooseADatasetAboveToConfigureAndRunAMachineLearningExperiment":
    "Choose a dataset above to configure and run a machine-learning experiment.",
  "ui.ml.coefficientOfDeterminationItCanBeNegative":
    "Coefficient of determination; it can be negative",
  "ui.ml.configureExperiment": "Configure experiment",
  "ui.ml.correctShareOfPredictedPositivesMulticlassUsesTheSavedAveragingMethod":
    "Correct share of predicted positives; multiclass uses the saved averaging method",
  "ui.ml.couldNotLoadColumns": "Could not load columns",
  "ui.ml.cvFoldCount": "CV fold count",
  "ui.ml.cvMean": "CV mean ·",
  "ui.ml.cvSdBetweenFoldsNotCi": "CV SD (between folds, not CI)",
  "ui.ml.dataSha256": "Data SHA256",
  "ui.ml.detectedShareOfActualPositivesMulticlassUsesTheSavedAveragingMethod":
    "Detected share of actual positives; multiclass uses the saved averaging method",
  "ui.ml.displayChangesOnlyNoRecalculationOrChangeToTheSavedCvScoringMetric":
    "Display changes only; no recalculation or change to the saved CV scoring metric.",
  "ui.ml.downloadModelArtifact": "Download model artifact",
  "ui.ml.dummyBaseline": "Dummy baseline ·",
  "ui.ml.f1": "F1",
  "ui.ml.features": "Features",
  "ui.ml.folds": "Folds",
  "ui.ml.goToData": "Go to Data",
  "ui.ml.groupVariable": "Group variable",
  "ui.ml.harmonicMeanOfPrecisionAndRecall":
    "Harmonic mean of precision and recall",
  "ui.ml.higherIsBetter": "Higher is better",
  "ui.ml.inspectFeatureImportanceValues": "Inspect feature-importance values",
  "ui.ml.inspectSavedConfigurationAndSoftwareVersions":
    "Inspect saved configuration and software versions",
  "ui.ml.inspectTheEquivalentValuesTable":
    "Inspect the equivalent values table",
  "ui.ml.inspectTheSavedResidualDistribution":
    "Inspect the saved residual distribution",
  "ui.ml.interp": "✓ interp",
  "ui.ml.loadRun": "Load Run",
  "ui.ml.loadSavedMlResults": "Load saved ML results",
  "ui.ml.loadingChart": "Loading chart…",
  "ui.ml.loadingColumns": "Loading columns…",
  "ui.ml.loadingMachineLearning": "Loading machine learning…",
  "ui.ml.logLoss": "Log loss",
  "ui.ml.logarithmicLossOfPredictedProbabilities":
    "Logarithmic loss of predicted probabilities",
  "ui.ml.lowerIntervalBound": "Lower interval bound",
  "ui.ml.lowerIsBetter": "Lower is better",
  "ui.ml.mae": "MAE",
  "ui.ml.mape": "MAPE",
  "ui.ml.meanAbsoluteErrorInTheTargetVariableSUnits":
    "Mean absolute error, in the target variable's units",
  "ui.ml.meanAbsolutePercentageErrorSavedAsARatioNotEstimableWhenTheTargetIsZero":
    "Mean absolute percentage error, saved as a ratio; not estimable when the target is zero",
  "ui.ml.meanRecallAcrossClasses": "Mean recall across classes",
  "ui.ml.meanSquaredErrorInSquaredTargetVariableUnits":
    "Mean squared error, in squared target-variable units",
  "ui.ml.minClassCountAfterHoldoutValue":
    " (min class count after holdout {value0})",
  "ui.ml.mlProjectIdentityIsMissing": "ML project identity is missing.",
  "ui.ml.mlRunFailed": "ML run failed",
  "ui.ml.model": "Model",
  "ui.ml.modelMetrics": "Model metrics",
  "ui.ml.modelMetricsTableHorizontallyScrollable":
    "Model metrics table, horizontally scrollable",
  "ui.ml.modelsCompareCandidates": "Models (compare candidates)",
  "ui.ml.mse": "MSE",
  "ui.ml.noAvailablePoints": ": no available points.",
  "ui.ml.noCategoricalColumnsAvailable": "No categorical columns available.",
  "ui.ml.noCompletedModelResultsCheckTheRunStatusAndFailureDetails":
    "No completed model results. Check the Run status and failure details.",
  "ui.ml.noNumericColumnsAvailable": "No numeric columns available.",
  "ui.ml.noTrustedFoldsSuggestionValue":
    "No trusted folds suggestion — {value0}.",
  "ui.ml.numericFeatures": "Numeric features",
  "ui.ml.oneHotEncoded": "(one-hot encoded)",
  "ui.ml.originalCurve": "Original curve",
  "ui.ml.otherDisplayedMetrics": "Other displayed metrics",
  "ui.ml.pointsSavedDeterministicSamplingRetainsBothEndpoints":
    " points; saved deterministic sampling retains both endpoints.",
  "ui.ml.prAuc": "PR-AUC",
  "ui.ml.precision": "Precision",
  "ui.ml.precisionRecall": "Precision–Recall",
  "ui.ml.predicted": "Predicted",
  "ui.ml.primaryDisplayedMetric": "Primary displayed metric",
  "ui.ml.rSquared": "R²",
  "ui.ml.randomSeed": "Random seed",
  "ui.ml.recall": "Recall",
  "ui.ml.requestCancellation": "Request cancellation",
  "ui.ml.requiredForGroupKFold": "*required for Group K-Fold",
  "ui.ml.requiredForTimeSeriesSplit": "*required for time-series split",
  "ui.ml.residualDistribution": "Residual distribution",
  "ui.ml.residualObservedPredictedSdIsThePopulationStandardDeviationOfTestSetResidualsNotAConfidenceInterval":
    "Residual = observed − predicted; SD is the population standard deviation of test-set residuals, not a confidence interval.",
  "ui.ml.rmse": "RMSE",
  "ui.ml.roc": "ROC",
  "ui.ml.rocAuc": "ROC-AUC",
  "ui.ml.rootMeanSquaredErrorInTheTargetVariableSUnits":
    "Root mean squared error, in the target variable's units",
  "ui.ml.rowsMustBeSortedAscendingByThisColumnUnsortedDataIsRejected":
    "Rows must be sorted ascending by this column; unsorted data is rejected.",
  "ui.ml.runSourceUnverifiedNoExperimentRecordMatchesThisRun":
    "Run source unverified: no experiment record matches this Run.",
  "ui.ml.sampleCount": "Sample count",
  "ui.ml.samplesTheFigureUsesThisModelSPredictionsAndContainsNoRawPerRowData":
    " samples. The figure uses this model's predictions and contains no raw per-row data.",
  "ui.ml.savedChecksDetectedPotentialDataLeakage":
    "Saved checks detected potential data leakage:",
  "ui.ml.savedConfusionMatrix": "Saved confusion matrix",
  "ui.ml.savedDataLeakageChecks": "Saved data-leakage checks",
  "ui.ml.savedExperiment": "Saved experiment",
  "ui.ml.savedFeatureImportance": "Saved feature importance",
  "ui.ml.savedFeatureImportanceValuesTable":
    "Saved feature-importance values table",
  "ui.ml.savedMlExperiments": "Saved ML experiments",
  "ui.ml.savedModelMetrics": "Saved model metrics",
  "ui.ml.savedPredictionDiagnostics": "Saved prediction diagnostics",
  "ui.ml.savedProposalParametersRetainedOnSubmission":
    "Saved proposal parameters (retained on submission)",
  "ui.ml.savedResultsCouldNotBeLoadedCheckTheSourceAndPermissions":
    "Saved results could not be loaded. Check the source and permissions.",
  "ui.ml.savedRun": "Saved Run",
  "ui.ml.savedRunAndSource": "Saved Run and source",
  "ui.ml.savedScoringRank": "Saved scoring rank",
  "ui.ml.savedSplitStrategy": "Saved split strategy",
  "ui.ml.savedTestSet": "Saved test set",
  "ui.ml.savedValue": "Saved value",
  "ui.ml.scale": "Scale",
  "ui.ml.selectADataset": "Select a dataset",
  "ui.ml.selectAGroupColumn": "Select a group column…",
  "ui.ml.selectATarget": "Select a target…",
  "ui.ml.selectATargetAtLeastOneFeatureAndAtLeastOneModel":
    "Select a target, at least one feature, and at least one model.",
  "ui.ml.selectATimeColumn": "Select a time column…",
  "ui.ml.selectAnExperimentForTheCurrentSourceVersion":
    "Select an experiment for the current source version",
  "ui.ml.shareClassifiedCorrectly": "Share classified correctly",
  "ui.ml.showValue": "Show {value0}",
  "ui.ml.source": ". Source:",
  "ui.ml.sourceDataset": "Source dataset",
  "ui.ml.statforgeWillNotFallBackToPlainKFoldIfTheGroupColumnIsMissing":
    "Quantelune will not fall back to plain K-Fold if the group column is missing.",
  "ui.ml.suggestedFoldsFromTrainingSubsetTargetStatistics":
    "Suggested folds from training-subset target statistics:",
  "ui.ml.targetVariable": "Target variable",
  "ui.ml.test": "Test ·",
  "ui.ml.testConfusionMatrix": "Test confusion matrix",
  "ui.ml.testPredictionDiagnostics": "Test prediction diagnostics",
  "ui.ml.testSetMetricsSavedByThisRunShownInModelRecordOrderCvMeansAndBetweenFoldSdAreListedSeparately":
    "Test-set metrics saved by this Run, shown in model record order. CV means and between-fold SD are listed separately.",
  "ui.ml.theBaselineWasNotCalculatedSuccessfullyNoMetricsAreFabricated":
    "The baseline was not calculated successfully; no metrics are fabricated.",
  "ui.ml.theCurrentSourceDatasetIsUnavailableOrBelongsToAnotherProject":
    "The current source dataset is unavailable or belongs to another project.",
  "ui.ml.theSavedRunDoesNotMatchTheCurrentSourceVersionOrPermissions":
    "The saved Run does not match the current source version or permissions.",
  "ui.ml.theSavedTestSetCannotEstimateThisCurveOrPredictionProbabilitiesWereNotSaved":
    ": the saved test set cannot estimate this curve, or prediction probabilities were not saved.",
  "ui.ml.theServiceCurrentlyStoresAveragePrecisionApInThisField":
    "The service currently stores average precision (AP) in this field",
  "ui.ml.theTableShowsTestSetResults": "· The table shows Test-set results.",
  "ui.ml.thereAreNoSavedMlExperimentsForTheCurrentSourceVersion":
    "There are no saved ML experiments for the current source version.",
  "ui.ml.thisLegacyRunHasNoSavedPredictionDiagnosticsViewingItDoesNotRetrainTheModel":
    "This legacy Run has no saved prediction diagnostics; viewing it does not retrain the model.",
  "ui.ml.thisModelHasNoAvailableFeatureImportance":
    "This model has no available feature importance.",
  "ui.ml.timeVariable": "Time variable",
  "ui.ml.trainEvaluate": "Train & evaluate",
  "ui.ml.trainingAndEvaluatingModels": "Training and evaluating models…",
  "ui.ml.trainingSeconds": "Training seconds",
  "ui.ml.trainingValidationTest": "Training / validation / test",
  "ui.ml.unavailableMeansMissingNotCalculatedOrNonFiniteRatherThan0CvIsTheMeanOfFoldScoresSdMeasuresDifferencesBetweenFoldsNotAConfidenceIntervalOrUncertaintyOfTestMetrics":
    "Unavailable means missing, not calculated or non-finite, rather than 0. CV is the mean of fold scores; SD measures differences between folds, not a confidence interval or uncertainty of Test metrics.",
  "ui.ml.uploadADatasetFirstThenBuildAReproducibleMlExperimentWithGuardedSplitsLeakageChecksAndMultiMetricComparison":
    "Upload a dataset first, then build a reproducible ML experiment with guarded splits, leakage checks and multi-metric comparison.",
  "ui.ml.upperIntervalBound": "Upper interval bound",
  "ui.ml.usesTheSameTrainingAndTestAllocationHigherPredictiveMetricsDoNotEstablishCausation":
    "Uses the same training and test allocation. Higher predictive metrics do not establish causation.",
  "ui.ml.validation": "Validation",
  "ui.ml.validationSetIndependentOfTest":
    "Validation set (independent of Test)",
  "ui.ml.winsorize": "Winsorize",
  "ui.preview.agentNotReadyConfigureTheModelKeyAndCostCeilingsThroughTheSecureSettingsEntryPointFirstManualAnalysisRemainsAvailable":
    "AGENT_NOT_READY · Configure the model, key and cost ceilings through the secure settings entry point first. Manual analysis remains available.",
  "ui.preview.allowEgress": "Allow egress",
  "ui.preview.allowValidLocalStatisticsMlExecutionForThisRequestOtherwiseProposeOnly":
    "Allow valid local statistics/ML execution for this request; otherwise propose only.",
  "ui.preview.analysisProposalNotYetCalculated":
    "Analysis proposal · not yet calculated",
  "ui.preview.approveThisQuestionSelectedSourceVersionsColumnsAndControlledToolSummaryEgressUsingTheModelAndLocalBudgetConfirmedInSettings":
    "Approve this question, selected source versions/columns and controlled tool-summary egress, using the model and local budget confirmed in settings.",
  "ui.preview.approvedLocalToolSummary": "Approved local tool summary ·",
  "ui.preview.businessCompletionIsDeterminedByTheActualRun":
    ". Business completion is determined by the actual Run.",
  "ui.preview.businessStatus": "Business status:",
  "ui.preview.calculatedFacts": "Calculated facts ·",
  "ui.preview.currency": "Currency",
  "ui.preview.data": "; data:",
  "ui.preview.dataSha": "Data SHA:",
  "ui.preview.eachRequestSendsOnlyTheCurrentQuestionSelectedCurrentSourceSummariesControlledToolParametersAndReturnedSummariesEarlierChatRawRowsAndMaterialsAreNotSentAutomatically":
    "Each request sends only the current question, selected current-source summaries, controlled tool parameters and returned summaries. Earlier chat, raw rows and materials are not sent automatically.",
  "ui.preview.editItAndApproveExecution": ", edit it and approve execution.",
  "ui.preview.effectiveSampleN": "; effective sample n=",
  "ui.preview.exactModel": "Exact model",
  "ui.preview.fillTheExistingFormForManualConfirmation":
    "Fill the existing form for manual confirmation",
  "ui.preview.glm": "GLM",
  "ui.preview.iConfirmTheExactModelAndCurrentInputOutputPriceCeilings":
    "I confirm the exact model and current input/output price ceilings.",
  "ui.preview.iUnderstandTheDifferenceBetweenTheLocalBudgetAndTheProviderBill":
    "I understand the difference between the local budget and the provider bill.",
  "ui.preview.inferredType": "; inferred type",
  "ui.preview.inputPriceCeilingMillionTokens":
    "Input price ceiling / million tokens",
  "ui.preview.interpretationSourceMismatchFieldsAreNotDisplayed":
    "Interpretation source mismatch; fields are not displayed.",
  "ui.preview.interruptedNeedsReviewNoAutomaticRerunInspectTheActualRun":
    "Interrupted / needs review: no automatic rerun; inspect the actual Run.",
  "ui.preview.limitations": "Limitations:",
  "ui.preview.localCallBudget": "Local call budget",
  "ui.preview.locateTheActualSourceRecord": "Locate the actual source record",
  "ui.preview.locateTheReadSource": "Locate the read source",
  "ui.preview.missing": ": missing",
  "ui.preview.modelCostAndApprovalSnapshotForThisTurnRealProviderUnverified":
    "Model, cost and approval snapshot for this turn (real Provider unverified)",
  "ui.preview.noResearchConclusionIsDrawnAutomaticallyFromThePValue":
    ". No research conclusion is drawn automatically from the p-value.",
  "ui.preview.opencodeZenModelsExplicitlyCompatibleWithChatCompletions":
    "OpenCode Zen (models explicitly compatible with Chat Completions)",
  "ui.preview.outputPriceCeilingMillionTokens":
    "Output price ceiling / million tokens",
  "ui.preview.p": "；p=",
  "ui.preview.physicalType": ": physical type",
  "ui.preview.primaryMetric": "Primary metric:",
  "ui.preview.project": "Project:",
  "ui.preview.provider": "Provider",
  "ui.preview.rawRowsLabelSamplesAndAutomaticResearchConclusionsAreNotDisplayed":
    "Raw rows, label samples and automatic research conclusions are not displayed.",
  "ui.preview.refreshSavedList": "Refresh saved list",
  "ui.preview.researchInterpretationFieldReferencesChecked":
    "Research interpretation · field references checked",
  "ui.preview.researchQuestion": "Research question",
  "ui.preview.reservationsAreNotRefundedOnFailureTimeoutOrMissingVerifiedUsageHiddenReasoningTokensAndProviderBillingHaveNotBeenTestedTheLocalBudgetDoesNotGuaranteeAHardLimitOnTheProviderBill":
    ". Reservations are not refunded on failure, timeout or missing verified usage. Hidden reasoning tokens and provider billing have not been tested; the local budget does not guarantee a hard limit on the provider bill.",
  "ui.preview.reservedCost": "Reserved cost:",
  "ui.preview.restartingReadsActualSavedRecordsInterruptedOrReviewPendingOperationsAreNotRepeatedAutomaticallyANewRequestIdDoesNotImplySemanticDeduplicationAcrossRequests":
    "Restarting reads actual saved records. Interrupted or review-pending operations are not repeated automatically; a new request ID does not imply semantic deduplication across requests.",
  "ui.preview.restrictedAiDevelopmentPreviewSecureConfiguration":
    "Restricted AI development preview · secure configuration",
  "ui.preview.reviewThisRequestSSourceAndEgressSummary":
    "Review this request's source and egress summary",
  "ui.preview.rowsReadLocally": "Rows read locally:",
  "ui.preview.run": "Run：",
  "ui.preview.saveSecureConfiguration": "Save secure configuration",
  "ui.preview.savedParametersLocalViewingOnly":
    "Saved parameters (local viewing only)",
  "ui.preview.savedResearchSessionsReadOnly":
    "Saved research sessions (read-only)",
  "ui.preview.savedStatus": "Saved status:",
  "ui.preview.secureKey": "Secure key",
  "ui.preview.selectTheSameDatasetOnTheCorrespondingStatisticsOrMlPageBeforeProposingOtherPagesAreNotChangedAutomatically":
    "Select the same dataset on the corresponding statistics or ML page before proposing; other pages are not changed automatically.",
  "ui.preview.sessionStatus": "Session status:",
  "ui.preview.submitThisRequest": "Submit this request",
  "ui.preview.theBoundedPlanIsSavedButNotCalculatedSelectItsRevisionInPlansAndTemplates":
    "The bounded plan is saved but not calculated. Select its revision in Plans and templates",
  "ui.preview.theKeyIsStoredInThisInstanceSWindowsCredentialsForTheCurrentUserThisEntryPointMakesNoModelRequestReEnterTheMatchingKeyWhenChangingProvidersEachQuestionStillRequiresApprovalOfTheQuestionCurrentDataVersionSelectedColumnsAndSummaryEgressScope":
    "The key is stored in this instance's Windows credentials for the current user; this entry point makes no model request. Re-enter the matching key when changing providers. Each question still requires approval of the question, current data version, selected columns and summary egress scope.",
  "ui.preview.theProposalSourceChangedTheFormCannotBePopulated":
    "The proposal source changed; the form cannot be populated.",
  "ui.preview.theResultSourceDoesNotMatchTheCurrentDataVersionValuesAreNotDisplayed":
    "The result source does not match the current data version; values are not displayed.",
  "ui.preview.theToolSummarySourceDoesNotMatchTheCurrentContextContentIsNotDisplayed":
    "The tool-summary source does not match the current context; content is not displayed.",
  "ui.preview.thereAreNoVisibleSavedSessionsForThisProject":
    "There are no visible saved sessions for this project.",
  "ui.preview.thisTemplateShowsSummariesActuallyReadAndSavedAfterThisTurnSApprovalAFinishedSessionDoesNotMeanANewBusinessRunWasCreatedModelFreeTextIsUnpublished":
    "This template shows summaries actually read and saved after this turn's approval; a finished session does not mean a new business Run was created. Model free text is unpublished.",
  "ui.preview.toolStatus": "Tool status:",
  "ui.preview.trustedTemplatesReadCalculationRecordsModelNaturalLanguageInterpretationsAreUnpublishedAndUnverified":
    "Trusted templates read calculation records; model natural-language interpretations are unpublished and unverified.",
  "ui.preview.valuesAndStatusesAreRenderedFromSavedRecordsModelGeneratedFactsOrCausalConclusionsAreUnpublished":
    "Values and statuses are rendered from saved records; model-generated facts or causal conclusions are unpublished.",
  "ui.preview.warning": "Warning:",
  "ui.preview.withMaterialCitationsAutomaticExecutionAcceptsOnlyTheApprovedCurrentValidFormOtherParametersMustBeFilledInForManualConfirmationFirst":
    "With material citations, automatic execution accepts only the approved current valid form; other parameters must be filled in for manual confirmation first.",
  "ui.profiling.alerts": "Alerts",
  "ui.profiling.algorithm": "algorithm",
  "ui.profiling.allNullColumns": "All-null columns",
  "ui.profiling.avgLength": "Avg length",
  "ui.profiling.boolean": "boolean",
  "ui.profiling.boolean.profilingOverview": "Boolean",
  "ui.profiling.categorical": "categorical",
  "ui.profiling.chooseADatasetAboveToInspectItsProfileOrRunANewOne":
    "Choose a dataset above to inspect its profile, or run a new one.",
  "ui.profiling.columnTypes": "Column types",
  "ui.profiling.completeRows": "Complete rows",
  "ui.profiling.completeness": "Completeness",
  "ui.profiling.computed": "computed",
  "ui.profiling.computing": "Computing…",
  "ui.profiling.computingProfile": "Computing profile…",
  "ui.profiling.constant": "constant",
  "ui.profiling.correlationMatrix": "correlation matrix",
  "ui.profiling.correlationMethod": "Correlation method",
  "ui.profiling.correlations": "Correlations",
  "ui.profiling.count": "Count",
  "ui.profiling.dataQuality": "Data quality",
  "ui.profiling.date": "date",
  "ui.profiling.datetime": "datetime",
  "ui.profiling.datetime.profilingOverview": "Datetime",
  "ui.profiling.distribution": "Distribution",
  "ui.profiling.duplicateRows": "Duplicate rows",
  "ui.profiling.duplicates": "Duplicates",
  "ui.profiling.earliest": "Earliest",
  "ui.profiling.empty": "empty",
  "ui.profiling.emptyProfile": "Empty profile",
  "ui.profiling.fileSize": "File size",
  "ui.profiling.float": "float",
  "ui.profiling.greyCellsIndicatePairsWithInsufficientOverlappingNonNullValuesBlueNegativeCorrelationRedPositiveCorrelation":
    "Grey cells indicate pairs with insufficient overlapping non-null values. Blue = negative correlation, red = positive correlation.",
  "ui.profiling.highMissingColumns50": "High-missing columns (>50%)",
  "ui.profiling.identifier": "identifier",
  "ui.profiling.integer": "integer",
  "ui.profiling.invalid": "Invalid",
  "ui.profiling.iqr": "IQR",
  "ui.profiling.isEntirelyEmpty": "is entirely empty.",
  "ui.profiling.isMissingMoreThan50OfValues":
    "is missing more than 50% of values.",
  "ui.profiling.latest": "Latest",
  "ui.profiling.loadingProfile": "Loading profile…",
  "ui.profiling.loadingProfiling": "Loading profiling…",
  "ui.profiling.max": "Max",
  "ui.profiling.maxLength": "Max length",
  "ui.profiling.mean": "Mean",
  "ui.profiling.median": "Median",
  "ui.profiling.min": "Min",
  "ui.profiling.minLength": "Min length",
  "ui.profiling.missing": "Missing",
  "ui.profiling.missingRateByColumn": "Missing rate by column",
  "ui.profiling.monotonic": "Monotonic",
  "ui.profiling.noDistributionData": "No distribution data.",
  "ui.profiling.noMissingValuesInThisDataset":
    "No missing values in this dataset.",
  "ui.profiling.noProfileYet": "No profile yet",
  "ui.profiling.noQualityAlertsForThisDataset":
    "No quality alerts for this dataset.",
  "ui.profiling.nulls": "Nulls",
  "ui.profiling.numeric": "Numeric",
  "ui.profiling.numericColumns": "numeric columns",
  "ui.profiling.other": "Other",
  "ui.profiling.outliers": "Outliers",
  "ui.profiling.overview": "Overview",
  "ui.profiling.p05": "P05",
  "ui.profiling.p95": "P95",
  "ui.profiling.physicalType": "Physical type",
  "ui.profiling.profiledOnASampleOf": "Profiled on a sample of",
  "ui.profiling.profilingFailed": "Profiling failed",
  "ui.profiling.q1": "Q1",
  "ui.profiling.q3": "Q3",
  "ui.profiling.quality": "Quality",
  "ui.profiling.recompute": "Recompute",
  "ui.profiling.rows": "Rows",
  "ui.profiling.rows.profilingOverview": "rows (",
  "ui.profiling.rowsByMissingValueCount": "Rows by missing-value count",
  "ui.profiling.runAProfileToComputePerColumnStatisticsMissingValueAnalysisCorrelationsAndADataQualityScore":
    "Run a profile to compute per-column statistics, missing-value analysis, correlations, and a data-quality score.",
  "ui.profiling.runProfiling": "Run profiling",
  "ui.profiling.sampleValues": "Sample values",
  "ui.profiling.sampled": "sampled (",
  "ui.profiling.skewness": "Skewness",
  "ui.profiling.spanDays": "Span (days)",
  "ui.profiling.stdDev": "Std dev",
  "ui.profiling.string": "string",
  "ui.profiling.string.profilingOverview": "String",
  "ui.profiling.theUnderlyingDataChangedSinceThisProfileWasComputedRecomputeToRefreshTheStatistics":
    "The underlying data changed since this profile was computed. Recompute to refresh the statistics.",
  "ui.profiling.thisProfileHasNoResultPayloadTryRecomputing":
    "This profile has no result payload. Try recomputing.",
  "ui.profiling.timeRange": "Time range",
  "ui.profiling.topValues": "Top values",
  "ui.profiling.totalStatisticsAreEstimates":
    "total). Statistics are estimates.",
  "ui.profiling.uniq": "uniq",
  "ui.profiling.unique": "Unique",
  "ui.profiling.unknown": "unknown",
  "ui.profiling.uploadADatasetFirstThenRunAProfileToExploreItsStructureAndQuality":
    "Upload a dataset first, then run a profile to explore its structure and quality.",
  "ui.projects.aboutThisProject": "About this project",
  "ui.projects.actions": "Actions",
  "ui.projects.allProjects": "All projects",
  "ui.projects.andAllOfItsDatasetsWillBePermanentlyRemovedThisCannotBeUndone":
    "and all of its datasets will be permanently removed. This cannot be undone.",
  "ui.projects.anomalyDetection": "Anomaly detection",
  "ui.projects.backToProjects": "Back to Projects",
  "ui.projects.binaryClassification": "Binary classification",
  "ui.projects.buildAnAnalysisWorkflow": "Build an analysis workflow",
  "ui.projects.cancel": "Cancel",
  "ui.projects.clustering": "Clustering",
  "ui.projects.computeSchemaMissingnessAndCorrelations":
    "Compute schema, missingness, and correlations.",
  "ui.projects.configureAMachineLearningExperimentInTheWorkbench":
    "Configure a machine-learning experiment in the workbench.",
  "ui.projects.configureAndRunStatisticalMethods":
    "Configure and run statistical methods.",
  "ui.projects.createAProjectToHoldYourDatasetsWorkflowsAndResults":
    "Create a project to hold your datasets, workflows and results.",
  "ui.projects.createAnEditableCopyOfThisFrozenProject":
    "Create an editable copy of this frozen project.",
  "ui.projects.created": "Created",
  "ui.projects.data": "Data",
  "ui.projects.dataSourcesContextExpectedDeliverables":
    "Data sources, context, expected deliverables…",
  "ui.projects.deleteThisProject": "Delete this project?",
  "ui.projects.deleteValue": "Delete {value0}",
  "ui.projects.description": "Description",
  "ui.projects.draftEditableProjectRunsAreAppendOnly":
    "· Draft — editable project, runs are append-only",
  "ui.projects.eGIrisMorphometricsStudy": "e.g. Iris morphometrics study",
  "ui.projects.editAndValidateAnAnalysisWorkflow":
    "Edit and validate an analysis workflow.",
  "ui.projects.everyChangeInStatforgeIsVersionedTheRevisionCounterOnThisProjectIncrementsAsAnalysisArtifactsAreCommitted":
    "Every change in Quantelune is versioned. The revision counter on this project increments as analysis artifacts are committed.",
  "ui.projects.exportIsUnavailableAReproduciblePackageIsNotYetImplemented":
    "Export is unavailable. A reproducible package is not yet implemented.",
  "ui.projects.exportZip": "Export ZIP",
  "ui.projects.failed": "Failed",
  "ui.projects.fork": "Fork",
  "ui.projects.forkEditableCopyOfAFrozenVersion":
    "· Fork — editable copy of a frozen version",
  "ui.projects.forkTitle": "Fork title",
  "ui.projects.forkedProjectsValue": "Forked → /projects/{value0}",
  "ui.projects.freeze": "Freeze",
  "ui.projects.freezeSnapshotFingerprintsArtifactsExportPage":
    "· Freeze — snapshot fingerprints + artifacts (Export page)",
  "ui.projects.frozen": "frozen",
  "ui.projects.frozen.versionsClient": "Frozen",
  "ui.projects.inspectTheSchemaProfile": "Inspect the schema profile",
  "ui.projects.itsDatasetsAndHistoryWereRemovedWithIt":
    "Its datasets and history were removed with it.",
  "ui.projects.loadingAgent": "Loading Agent…",
  "ui.projects.loadingProject": "Loading project…",
  "ui.projects.loadingProjectStatus": "Loading project status…",
  "ui.projects.loadingProjects": "Loading projects…",
  "ui.projects.loadingVersions": "Loading versions…",
  "ui.projects.lockThisProjectSnapshotAllDataFingerprintsAndMakeItReproducible":
    "Lock this project, snapshot all data fingerprints, and make it reproducible.",
  "ui.projects.machineLearning": "Machine learning",
  "ui.projects.manageData": "Manage data",
  "ui.projects.metricsCalibrationCurvesAndErrorAnalysisSideBySideForEveryCandidateModelInTheProject":
    "Metrics, calibration curves and error analysis side by side for every candidate model in the project.",
  "ui.projects.modelEvaluation": "Model Evaluation",
  "ui.projects.multiclassClassification": "Multiclass classification",
  "ui.projects.nameTheStudyYouCanAddDataNext":
    "Name the study — you can add data next.",
  "ui.projects.nextSteps": "Next steps",
  "ui.projects.noDataOnTheBenchYetUploadACsvExcelOrParquetFileToBegin":
    "No data on the bench yet — upload a CSV, Excel or Parquet file to begin.",
  "ui.projects.noProjectsYet": "No projects yet",
  "ui.projects.notSpecifiedYet": "Not specified yet",
  "ui.projects.openValue": "Open {value0}",
  "ui.projects.optional": "(optional)",
  "ui.projects.preparingAFreshBench": "Preparing a fresh bench…",
  "ui.projects.profiling": "Profiling",
  "ui.projects.project": "Project",
  "ui.projects.projectCreated": "Project created",
  "ui.projects.projectDeleted": "Project deleted",
  "ui.projects.projectFrozen": "Project frozen.",
  "ui.projects.quality": "Quality",
  "ui.projects.researchQuestion": "Research question",
  "ui.projects.restrictedAgentPreview": "Restricted Agent preview",
  "ui.projects.rev": "Rev",
  "ui.projects.reviewTheCurrentProjectContextInTheRightWorkbenchConfigureModelsAndCostsThroughTheSecureSettingsEntryPointAndApproveEachEgressRequestStatisticsAndMlPagesCanUseCurrentDataFormsAndActualResultReferencesManualAnalysisRemainsAvailableWithoutConfiguration":
    "Review the current project context in the right workbench, configure models and costs through the secure settings entry point, and approve each egress request. Statistics and ML pages can use current data, forms and actual-result references; manual analysis remains available without configuration.",
  "ui.projects.revision": "Revision",
  "ui.projects.runDeterministicDataQualityRules":
    "Run deterministic data-quality rules.",
  "ui.projects.sectionError": "Section error",
  "ui.projects.selectATaskType": "Select a task type",
  "ui.projects.statisticalAnalysis": "Statistical analysis",
  "ui.projects.statistics": "Statistics",
  "ui.projects.status": "Status",
  "ui.projects.taskType": "Task type",
  "ui.projects.thesePagesProvideTheCurrentlyVerifiableManualWorkflows":
    "These pages provide the currently verifiable manual workflows.",
  "ui.projects.thisIsARestrictedLocalPreviewModelCallsAndProviderBillingAreUnverifiedUntilASeparatelyApprovedRealIntegrationTestItDoesNotConstituteGateCOrReleaseApproval":
    "This is a restricted local preview. Model calls and provider billing are unverified until a separately approved real integration test. It does not constitute Gate C or release approval.",
  "ui.projects.thisSectionEncounteredAnErrorOtherPartsOfStatforgeAreStillWorkingNormally":
    "This section encountered an error. Other parts of Quantelune are still working normally.",
  "ui.projects.updated": "Updated",
  "ui.projects.uploadADataset": "Upload a dataset",
  "ui.projects.uploadAndInspectADataset": "Upload and inspect a dataset.",
  "ui.projects.useTheExistingWorkbenches": "Use the existing workbenches",
  "ui.projects.valueValueOnTheBench": "{value0} {value1} on the bench",
  "ui.projects.versionStatus": "Version status",
  "ui.projects.versioning": "Versioning",
  "ui.projects.whatHypothesisWillThisProjectTest":
    "What hypothesis will this project test?",
  "ui.projects.workflow": "Workflow",
  "ui.projects.workspaceIndex": "Workspace index",
  "ui.quality.advancedSettings": "Advanced settings",
  "ui.quality.affectedColumns": "Affected columns",
  "ui.quality.affectedRatio": "Affected ratio",
  "ui.quality.affectedRows": "Affected rows",
  "ui.quality.allCategories": "All categories",
  "ui.quality.allSeverities": "All severities",
  "ui.quality.allStatuses": "All statuses",
  "ui.quality.backToLatest": "Back to latest",
  "ui.quality.category": "Category",
  "ui.quality.checked": "checked",
  "ui.quality.chooseADatasetAboveToReviewItsLatestQualityRunOrStartANewOne":
    "Choose a dataset above to review its latest quality run, or start a new one.",
  "ui.quality.clear": "Clear",
  "ui.quality.clearAllFilters": "Clear all filters",
  "ui.quality.clearFilters": "Clear filters",
  "ui.quality.columnName": "Column name…",
  "ui.quality.dataQualityScore": "Data quality score",
  "ui.quality.duplicateRowRate": "Duplicate row rate",
  "ui.quality.enableOhlcvCryptoRules": "Enable OHLCV / crypto rules",
  "ui.quality.error": "error",
  "ui.quality.erroredAndTheirResultsAreUnavailable":
    "errored and their results are unavailable.",
  "ui.quality.errors": "Errors",
  "ui.quality.everyRunIncludingRecomputesWithDifferentThresholdsIsKeptHereSoYouCanCompareScoresOverTime":
    "Every run (including recomputes with different thresholds) is kept here so you can compare scores over time.",
  "ui.quality.evidence": "Evidence",
  "ui.quality.failed": "failed",
  "ui.quality.filterByAffectedColumn": "Filter by affected column",
  "ui.quality.filterByCategory": "Filter by category",
  "ui.quality.filterBySeverity": "Filter by severity",
  "ui.quality.filterByStatus": "Filter by status",
  "ui.quality.finding": "finding",
  "ui.quality.findings": "Findings",
  "ui.quality.highCardinalityRatio": "High cardinality ratio",
  "ui.quality.highMissingness": "High missingness",
  "ui.quality.history": "History",
  "ui.quality.howToFixIt": "How to fix it",
  "ui.quality.keywordNarrowsTheCurrentPageOnly":
    "(keyword narrows the current page only)",
  "ui.quality.latest": "latest",
  "ui.quality.limitations": "Limitations",
  "ui.quality.loadQualityRunFromValue": "Load quality run from {value0}",
  "ui.quality.loadingFindings": "Loading findings…",
  "ui.quality.loadingQuality": "Loading quality…",
  "ui.quality.loadingQualityRun": "Loading quality run…",
  "ui.quality.loadingRunHistory": "Loading run history…",
  "ui.quality.maxColumns": "Max columns",
  "ui.quality.more": "more",
  "ui.quality.nearConstantRatio": "Near-constant ratio",
  "ui.quality.next": "Next",
  "ui.quality.noDeductionsEveryRuleStayedWithinItsThresholds":
    "No deductions — every rule stayed within its thresholds.",
  "ui.quality.noQualityCheckYet": "No quality check yet",
  "ui.quality.noQualityRunsYet": "No quality runs yet",
  "ui.quality.noScoreWasProducedForThisRun":
    "No score was produced for this run.",
  "ui.quality.obs": "obs",
  "ui.quality.observed": "Observed",
  "ui.quality.outlierRatio": "Outlier ratio",
  "ui.quality.page": "Page",
  "ui.quality.passed": "passed",
  "ui.quality.passed.qualityOverview": "Passed",
  "ui.quality.previous": "Previous",
  "ui.quality.qualityChecksInProgress": "Quality checks in progress…",
  "ui.quality.qualityRunFailed": "Quality run failed",
  "ui.quality.rows": "rows ·",
  "ui.quality.rule": "rule",
  "ui.quality.ruleOutcomes": "Rule outcomes",
  "ui.quality.ruleV": "rule v",
  "ui.quality.rules": "rules",
  "ui.quality.ruleset": "ruleset",
  "ui.quality.run": "run",
  "ui.quality.runQualityCheck": "Run quality check",
  "ui.quality.runTheDeterministicRuleEngineToScoreCompletenessValidityUniquenessConsistencyTemporalBehaviorAndUsabilityWithAFullFindingTrail":
    "Run the deterministic rule engine to score completeness, validity, uniqueness, consistency, temporal behavior, and usability — with a full finding trail.",
  "ui.quality.runWithTheseSettings": "Run with these settings",
  "ui.quality.runningQualityChecks": "Running quality checks…",
  "ui.quality.sampled": "sampled",
  "ui.quality.score": "score",
  "ui.quality.scoreDeductions": "Score deductions",
  "ui.quality.scoresAndFindingsReflectTheDataAndThresholdsAtThatTime":
    ". Scores and findings reflect the data and thresholds at that time.",
  "ui.quality.searchFindingsByKeyword": "Search findings by keyword",
  "ui.quality.searchTitleOrSummary": "Search title or summary…",
  "ui.quality.severity": "Severity",
  "ui.quality.skipped": "skipped",
  "ui.quality.skipped.qualityOverview": "Skipped",
  "ui.quality.structureCheckFailsAboveThisColumnCount":
    "Structure check fails above this column count",
  "ui.quality.thisDatasetWasSampledAffectedCountsAndRatiosAreEstimates":
    "This dataset was sampled — affected counts and ratios are estimates.",
  "ui.quality.thisRunCompletedBut": "This run completed, but",
  "ui.quality.thr": "thr",
  "ui.quality.threshold": "Threshold",
  "ui.quality.thresholdsRulePacks": "— thresholds & rule packs",
  "ui.quality.totalRules": "Total rules",
  "ui.quality.uploadADatasetFirstThenRunAQualityCheckToScoreItsCompletenessValidityUniquenessAndMore":
    "Upload a dataset first, then run a quality check to score its completeness, validity, uniqueness, and more.",
  "ui.quality.valueMustBeANumber": "{value0} must be a number.",
  "ui.quality.valueMustBeBetween0And1": "{value0} must be between 0 and 1.",
  "ui.quality.viewDetailsValue": "View details: {value0}",
  "ui.quality.viewing": "viewing",
  "ui.quality.viewingAHistoricalRunFrom": "Viewing a historical run from",
  "ui.quality.warn": "warn",
  "ui.quality.warningMissingness": "Warning missingness",
  "ui.quality.warnings": "Warnings",
  "ui.quality.whatThisRuleChecks": "What this rule checks",
  "ui.quality.whyItTriggered": "Why it triggered",
  "ui.report.assumptionChecks": "Assumption checks",
  "ui.report.completed": "Completed",
  "ui.report.confidenceInterval": "Confidence interval",
  "ui.report.confidenceLevel": "Confidence level",
  "ui.report.datasetId": "Dataset ID",
  "ui.report.datasetListIsMalformedAndCannotBeDisplayed":
    "Dataset list is malformed and cannot be displayed.",
  "ui.report.datasetSha256": "Dataset SHA-256",
  "ui.report.descriptiveSummaries": "Descriptive summaries",
  "ui.report.effectSize": "Effect size",
  "ui.report.estimates": "Estimates",
  "ui.report.excludedN": "Excluded n",
  "ui.report.exclusionReason": "Exclusion reason",
  "ui.report.finalN": "Final n",
  "ui.report.inferenceMetadata": "Inference metadata",
  "ui.report.loadingReportProject": "Loading report project…",
  "ui.report.loadingReports": "Loading reports…",
  "ui.report.loadingSavedReport": "Loading saved report…",
  "ui.report.loadingSavedRuns": "Loading saved runs…",
  "ui.report.methodVersion": "Method version",
  "ui.report.methodsetVersion": "Methodset version",
  "ui.report.missingPolicy": "Missing policy",
  "ui.report.noCompletedReportIsAvailable":
    "; no completed report is available.",
  "ui.report.noReadyDatasetsAreAvailableForThisProject":
    "No ready datasets are available for this project.",
  "ui.report.noSavedStatisticalRunsOnThisPage":
    "No saved statistical runs on this page.",
  "ui.report.noneRecorded": "None recorded",
  "ui.report.notRecorded": "Not recorded",
  "ui.report.originalN": "Original n",
  "ui.report.pValue": "p-value",
  "ui.report.provenanceAndParameters": "Provenance and parameters",
  "ui.report.readOnlyViewsOfPersistedStatisticalRunsNoAnalysisIsStartedHere":
    "Read-only views of persisted statistical runs. No analysis is started here.",
  "ui.report.reportProjectIdentityIsMissing":
    "Report project identity is missing.",
  "ui.report.runHistoryPages": "Run history pages",
  "ui.report.runId": "Run ID",
  "ui.report.sampleSize": "Sample size",
  "ui.report.savedRun": "Saved run",
  "ui.report.savedRunHistoryIsMalformedAndCannotBeDisplayed":
    "Saved run history is malformed and cannot be displayed.",
  "ui.report.savedRuns": "Saved runs",
  "ui.report.savedStatisticalReport": "Saved statistical report",
  "ui.report.savedStatisticalReports": "Saved statistical reports",
  "ui.report.selectADatasetToLoadItsSavedRuns":
    "Select a dataset to load its saved runs.",
  "ui.report.selectASavedRunToInspectItsRecord":
    "Select a saved run to inspect its record.",
  "ui.report.selectedRun": "Selected run",
  "ui.report.softwareVersions": "Software versions",
  "ui.report.started": "Started",
  "ui.report.statistic": "Statistic",
  "ui.report.storedApiValuesAreShownWithoutDisplayRoundingThisViewDoesNotRerunTheAnalysis":
    "Stored API values are shown without display rounding. This view does not rerun the analysis.",
  "ui.report.storedInputParameters": "Stored input parameters",
  "ui.report.storedPerVariableMissingCounts":
    "Stored per-variable missing counts",
  "ui.report.theSavedRunCouldNotBeLoaded": "The saved run could not be loaded.",
  "ui.report.theSelectedHistoryRecordBelongsToADifferentDataset":
    "The selected history record belongs to a different dataset.",
  "ui.report.thisRunIs": "This run is",
  "ui.report.threshold": "Threshold:",
  "ui.report.value": "Value:",
  "ui.report.viewSavedRun": "View saved run",
  "ui.research.algorithmSeed": "Algorithm / seed",
  "ui.research.analysesAndFigures": "Analyses and figures",
  "ui.research.analysisName": "Analysis name",
  "ui.research.analysisView": "Analysis view",
  "ui.research.auditConfigurationAndFullSavedIdentity":
    "Audit: configuration and full saved identity",
  "ui.research.binCount": "Bin count",
  "ui.research.caption": "Caption",
  "ui.research.closeInspector": "Close inspector",
  "ui.research.closeObjectTab": "Close object tab",
  "ui.research.coefficientBeta": "Coefficient β",
  "ui.research.coefficientBetaValue": "Coefficient β{value0}",
  "ui.research.continueYourResearch": "Continue your research",
  "ui.research.continuous": "Continuous",
  "ui.research.copyFigure": "Copy figure",
  "ui.research.createAnalysis": "Create analysis",
  "ui.research.dataForANewAnalysis": "Data for a new analysis",
  "ui.research.dataSha": "· data SHA",
  "ui.research.dataSha256": "data SHA256",
  "ui.research.descriptiveStatisticsCanSaveFullDistributionsCorrelationCanSaveScatterPlotsFromTheSameSourceOlsCanSaveCoefficientPlotsAfterAnActualRunSelectSaveFigureLegacyWelchRecordsWithoutCisShowGroupMeansOnly":
    "Descriptive statistics can save full distributions; correlation can save scatter plots from the same source; OLS can save coefficient plots. After an actual Run, select Save figure. Legacy Welch records without CIs show group means only.",
  "ui.research.displayLayer": "Display layer",
  "ui.research.distributionVariable": "Distribution variable",
  "ui.research.effectiveN": "Effective n",
  "ui.research.encodedLevels": "Encoded levels",
  "ui.research.export": "Export",
  "ui.research.fieldsPhysicalTypesAndMeasurementScales":
    "Fields: physical types and measurement scales",
  "ui.research.figureName": "Figure name",
  "ui.research.figureSampleAndSnapshotSource":
    "Figure sample and snapshot source",
  "ui.research.fullNValueValueBinsTukey15IqrOutliersValueValue":
    "Full n={value0} · {value1} bins · Tukey 1.5 IQR · outliers {value2}/{value3}",
  "ui.research.importData": "Import data",
  "ui.research.importDataToStartAnAnalysisConfigureCalculateReadTablesAndFiguresAndSaveFiguresInOneObjectOpenTheAiAssistantWhenNeeded":
    "Import data to start an analysis. Configure, calculate, read tables and figures, and save figures in one object. Open the AI assistant when needed.",
  "ui.research.inspectorWidth": "Inspector width",
  "ui.research.items": "items",
  "ui.research.localHtmlIncludesSavedScatterPointsObservationCoordinates":
    "Local HTML includes saved scatter points (observation coordinates)",
  "ui.research.lowerCiBound": "Lower CI bound",
  "ui.research.lowerMeanCiBound": "Lower mean CI bound",
  "ui.research.materialVersionsAreFixedWithTheFigureSourceToChangeCitationsReturnToTheSourceAnalysisAndRegenerate":
    "Material versions are fixed with the figure source. To change citations, return to the source analysis and regenerate.",
  "ui.research.maximum": "Maximum",
  "ui.research.mean": "Mean",
  "ui.research.meanDifferenceCiValueValueWelchSatterthwaiteGroupMeanPointsDoNotRepresentGroupMeanCis":
    "Mean-difference CI · {value0}% · {value1} · Welch–Satterthwaite. Group-mean points do not represent group-mean CIs.",
  "ui.research.median": "Median",
  "ui.research.methodAndSource": "Method and source:",
  "ui.research.minimum": "Minimum",
  "ui.research.missingN": "Missing n",
  "ui.research.mmPngStoresDpiWhileSvgRetainsVectorsAndFontFallback":
    " mm; PNG stores DPI, while SVG retains vectors and font fallback.",
  "ui.research.newAnalysis": "New analysis",
  "ui.research.noDiagnosticsWereReturned": "No diagnostics were returned.",
  "ui.research.noSavedResultsYetOpenParametersSelectAMethodAndVariablesThenRunANewCalculation":
    "No saved results yet. Open parameters, select a method and variables, then run a new calculation.",
  "ui.research.nominal": "Nominal",
  "ui.research.observationUnit": "Observation unit",
  "ui.research.olsCoefficientsSavedConfidenceLevelValue":
    "OLS coefficients · saved confidence level {value0}%",
  "ui.research.olsNValueValueReferenceLineBeta0":
    "OLS · n={value0} · {value1} · reference line β=0",
  "ui.research.openObjects": "Open objects",
  "ui.research.openOriginalRun": "Open original Run",
  "ui.research.ordinal": "Ordinal",
  "ui.research.originalNValueExcludedValueEffectiveNValueValue":
    "Original n={value0} · excluded {value1} · effective n={value2} · {value3}",
  "ui.research.p": "p",
  "ui.research.paper": "Paper",
  "ui.research.physicalDimensions": "Physical dimensions",
  "ui.research.projectIdentityIsMissing": "Project identity is missing.",
  "ui.research.projectWorkspace": "Project workspace",
  "ui.research.referenceGroup": "Reference group",
  "ui.research.research": "Research",
  "ui.research.restoringSavedState": "Restoring saved state…",
  "ui.research.reviewSourceData": "Review source data",
  "ui.research.rowValue": "Row {value0}",
  "ui.research.sampleRule": "Sample rule",
  "ui.research.save": "Save",
  "ui.research.saveTheDisplayRevisionBeforeExportNoStatisticalRunIsAdded":
    "Save the display revision before export; no statistical Run is added.",
  "ui.research.saveTheFigureBeforeEditingItsStyle":
    "Save the figure before editing its style.",
  "ui.research.savedCiValue": "Saved CI {value0}%",
  "ui.research.savedDiagnostics": "Saved diagnostics",
  "ui.research.savedFisherZIntervalValue":
    "Saved Fisher z interval · {value0}%.",
  "ui.research.savedRValue": "Saved r{value0}",
  "ui.research.savedStandardizeRequestValueThisOlsEngineDidNotStandardizeSoCoefficientsUseTheOriginalVariableScales":
    "Saved standardize request: {value0}; this OLS engine did not standardize, so coefficients use the original variable scales.",
  "ui.research.sd": "SD",
  "ui.research.se": "SE",
  "ui.research.selectAnAnalysisObjectToEditCalculationParametersEditFigureDisplayParametersInStyle":
    "Select an analysis object to edit calculation parameters. Edit figure display parameters in Style.",
  "ui.research.selectDataAndCreateAnAnalysis":
    "Select data and create an analysis",
  "ui.research.selectSha256": "Select SHA256",
  "ui.research.sharedInspector": "Shared inspector",
  "ui.research.showingValueValuePairsSeedValueSavedValueNoAdditionalFitting":
    "Showing {value0}/{value1} pairs · seed={value2} · saved {value3} · no additional fitting",
  "ui.research.singleAnalysisOutput": "Single-analysis output",
  "ui.research.snapshotValueDataSha256ValueValue":
    "Snapshot {value0} · data SHA256 {value1} · {value2}",
  "ui.research.t": "t",
  "ui.research.theCompleteResearchZipIsNotReady":
    "The complete research ZIP is not ready.",
  "ui.research.theCurrentDraftMayDifferFromSavedRunParametersOnlyANewCalculationCreatesANewRunDisplayEditsDoNotRewriteResults":
    "The current draft may differ from saved Run parameters. Only a new calculation creates a new Run; display edits do not rewrite results.",
  "ui.research.theScientificFigureWasNotSavedValue":
    "The scientific figure was not saved. {value0}",
  "ui.research.theUserExplicitlySelectedLocalInclusionOfSavedScatterPointsTheFigureContainsValueValueObservationCoordinates":
    "The user explicitly selected local inclusion of saved scatter points; the figure contains {value0}/{value1} observation coordinates.",
  "ui.research.theme": "Theme",
  "ui.research.unconfirmed": "Unconfirmed",
  "ui.research.unit": "Unit",
  "ui.research.upperCiBound": "Upper CI bound",
  "ui.research.upperMeanCiBound": "Upper mean CI bound",
  "ui.research.variable": "Variable",
  "ui.settings.aiProvider": "AI provider",
  "ui.settings.applicationData": "Application data",
  "ui.settings.database": "Database",
  "ui.settings.datasetStorage": "Dataset storage",
  "ui.settings.english": "English",
  "ui.settings.interfaceLanguage": "Interface language",
  "ui.settings.loadingSettings": "Loading settings…",
  "ui.settings.localRuntime": "Local runtime",
  "ui.settings.pathsAndReadinessChecksAreShownHereCredentialsAndThePerLaunchSessionTokenAreNeverDisplayed":
    "Paths and readiness checks are shown here; credentials and the per-launch session token are never displayed.",
  "ui.settings.refresh": "Refresh",
  "ui.settings.runningLocalChecks": "Running local checks…",
  "ui.settings.runtime": "Runtime",
  "ui.settings.simplifiedChinese": "Simplified Chinese",
  "ui.settings.statforgeApi": "Quantelune API",
  "ui.settings.systemDiagnostics": "System diagnostics",
  "ui.settings.useTheInstallerChoiceOrSelectALanguageForThisDevice":
    "Use the installer choice or select a language for this device.",
  "ui.statistics.actual0": "Actual 0",
  "ui.statistics.actual1": "Actual 1",
  "ui.statistics.additionalProposalParametersRetainedOnSubmission":
    "Additional proposal parameters (retained on submission)",
  "ui.statistics.adjRSquared": "Adj R²",
  "ui.statistics.aic": "AIC",
  "ui.statistics.alternativeHypothesis": "Alternative hypothesis",
  "ui.statistics.analysisFailed": "Analysis failed",
  "ui.statistics.assumptionsAndLimitations": "Assumptions and limitations",
  "ui.statistics.bic": "BIC",
  "ui.statistics.categoricalEncoding": "Categorical encoding",
  "ui.statistics.categoricalPredictors": "Categorical predictors",
  "ui.statistics.chooseADatasetAboveToConfigureAndRunAStatisticalAnalysis":
    "Choose a dataset above to configure and run a statistical analysis.",
  "ui.statistics.coefficientTable": "Coefficient table",
  "ui.statistics.collinearityVif": "Collinearity (VIF)",
  "ui.statistics.columnsAvailable": "columns available",
  "ui.statistics.completeStatisticalMethodCatalog":
    "Complete statistical method catalog",
  "ui.statistics.computingStatistics": "Computing statistics…",
  "ui.statistics.confidenceInterval": "Confidence interval",
  "ui.statistics.configureAMethodAboveAndRunYourFirstAnalysisResultsIncludeTheTestStatisticPValueConfidenceIntervalEffectSizeAndAssumptionChecks":
    "Configure a method above and run your first analysis. Results include the test statistic, p-value, confidence interval, effect size, and assumption checks.",
  "ui.statistics.configureAnalysis": "Configure analysis",
  "ui.statistics.confusionMatrixThreshold": "Confusion matrix (threshold =",
  "ui.statistics.converged": "Converged",
  "ui.statistics.currentMethod": "Current method:",
  "ui.statistics.cvRSquaredMean": "CV R² mean",
  "ui.statistics.cvRSquaredStd": "CV R² std",
  "ui.statistics.descriptiveStatistics": "Descriptive statistics",
  "ui.statistics.effectSize": "Effect size",
  "ui.statistics.effectiveN": "· effective n=",
  "ui.statistics.encodedLevels": "; encoded levels =",
  "ui.statistics.errorOnMissing": "Error on missing",
  "ui.statistics.excluded": "· excluded",
  "ui.statistics.fPValue": "F p-value",
  "ui.statistics.fullSavedRecordUnknownLegacyStructuresRemainReadable":
    "Full saved record (unknown legacy structures remain readable)",
  "ui.statistics.greater": "Greater (>)",
  "ui.statistics.groupingVariable": "Grouping variable",
  "ui.statistics.justRan": "✓ just ran",
  "ui.statistics.l1Ratio0Ridge1Lasso": "L1 ratio (0=ridge … 1=lasso)",
  "ui.statistics.label95Ci": "95% CI",
  "ui.statistics.less": "Less (<)",
  "ui.statistics.listwiseDeletion": "Listwise deletion",
  "ui.statistics.loadingChart": "Loading chart…",
  "ui.statistics.loadingHistory": "Loading history…",
  "ui.statistics.loadingLatestRun": "Loading latest run…",
  "ui.statistics.loadingStatistics": "Loading statistics…",
  "ui.statistics.method": "Method:",
  "ui.statistics.missingValues": "Missing values",
  "ui.statistics.modelSummary": "Model summary",
  "ui.statistics.n": "n",
  "ui.statistics.no": "No",
  "ui.statistics.noMethodsAvailable": "No methods available.",
  "ui.statistics.noQQData": "No Q-Q data.",
  "ui.statistics.noResidualData": "No residual data.",
  "ui.statistics.noResultRecordedForThisRun":
    "No result recorded for this run.",
  "ui.statistics.noStatisticalRunYet": "No statistical run yet",
  "ui.statistics.noStatisticalRunsYetForThisDataset":
    "No statistical runs yet for this dataset.",
  "ui.statistics.normalQQ": "Normal Q-Q",
  "ui.statistics.notReturned": "Not returned.",
  "ui.statistics.oneHotEncodedReferenceLevelDroppedAutomatically":
    "(one-hot encoded; reference level dropped automatically)",
  "ui.statistics.originalN": "Original n=",
  "ui.statistics.p": "p：",
  "ui.statistics.pairwiseDeletion": "Pairwise deletion",
  "ui.statistics.policy": "policy",
  "ui.statistics.pred0": "Pred 0",
  "ui.statistics.pred1": "Pred 1",
  "ui.statistics.pseudoRSquared": "Pseudo R²",
  "ui.statistics.reRun": "Re-run",
  "ui.statistics.reference": "reference =",
  "ui.statistics.referenceValue": "Reference value (μ₀)",
  "ui.statistics.regularizationAlpha": "Regularization α",
  "ui.statistics.residualsVsFitted": "Residuals vs fitted",
  "ui.statistics.result": "Result",
  "ui.statistics.runAnalysis": "Run analysis",
  "ui.statistics.runAt": "Run at",
  "ui.statistics.savedDescriptiveStatistics": "Saved descriptive statistics",
  "ui.statistics.savedParametersAndVariableRoles":
    "Saved parameters and variable roles",
  "ui.statistics.searchMethodNameOrCategory": "Search method name or category",
  "ui.statistics.searchStatisticalMethods": "Search statistical methods",
  "ui.statistics.select": "(select",
  "ui.statistics.selectAGroupingColumn": "Select a grouping column…",
  "ui.statistics.selectATargetColumn": "Select a target column…",
  "ui.statistics.selectValueValuePredictorSToRun":
    "Select {value0}–{value1} predictor(s) to run.",
  "ui.statistics.statisticalAnalysisInProgress":
    "Statistical analysis in progress…",
  "ui.statistics.statisticsProjectIdentityIsMissing":
    "Statistics project identity is missing.",
  "ui.statistics.status": "Status:",
  "ui.statistics.stdError": "Std. error",
  "ui.statistics.targetOutcomeVariable": "Target (outcome) variable",
  "ui.statistics.term": "Term",
  "ui.statistics.testsAndEstimates": "Tests and estimates",
  "ui.statistics.theOriginalRecordDidNotReturnThisTable":
    "The original record did not return this table.",
  "ui.statistics.theOriginalRecordReturnedNoConfidenceIntervalOpeningTheRecordDoesNotRecalculateIt":
    "The original record returned no confidence interval; opening the record does not recalculate it.",
  "ui.statistics.theOriginalRecordReturnedNoPValueNoSignificanceJudgmentIsMade":
    "The original record returned no p-value; no significance judgment is made.",
  "ui.statistics.theSavedRunDoesNotMatchTheCurrentSourceIdentityOrVersion":
    "The saved Run does not match the current source identity or version.",
  "ui.statistics.thisLegacyPageProvidesNoSavedInputsCheckVariableRolesAndMissingValueRequestsInTheOriginalRun":
    "This legacy page provides no saved inputs; check variable roles and missing-value requests in the original Run.",
  "ui.statistics.trainRSquared": "Train R²",
  "ui.statistics.twoSided": "Two-sided (≠)",
  "ui.statistics.uploadADatasetFirstThenRunDescriptiveStatisticsCorrelationsTTestsChiSquareAnovaAndMoreEachWithAssumptionChecksAndEffectSizes":
    "Upload a dataset first, then run descriptive statistics, correlations, t-tests, chi-square, ANOVA and more — each with assumption checks and effect sizes.",
  "ui.statistics.variableGroup": "Variable / group",
  "ui.statistics.view": "View",
  "ui.statistics.vif10SuggestsMulticollinearity":
    "VIF > 10 suggests multicollinearity.",
  "ui.workflow.addADatasetSourceConnectAStatisticsOrModelStepThenChooseAnOutputSaveTheGraphToResumeItLater":
    "Add a Dataset source, connect a statistics or model step, then choose an output. Save the graph to resume it later.",
  "ui.workflow.addAStep": "Add a step",
  "ui.workflow.buildAReproducibleAnalysis": "Build a reproducible analysis",
  "ui.workflow.bytes": "bytes",
  "ui.workflow.cancelRun": "Cancel run",
  "ui.workflow.cancellationRequestedReviewHistoryToCheckWhetherPublicationCompleted":
    "Cancellation requested. Review history to check whether publication completed.",
  "ui.workflow.checkSavedHistoryBeforeRetrying":
    "Check saved history before retrying.",
  "ui.workflow.chooseADataset": "Choose a dataset",
  "ui.workflow.chooseANode": "Choose a node",
  "ui.workflow.columnSchemaIsUnavailableCheckDatasetAccess":
    "Column schema is unavailable. Check dataset access.",
  "ui.workflow.connect": "Connect",
  "ui.workflow.connectNodesWithTheKeyboard": "Connect nodes with the keyboard",
  "ui.workflow.connectionSource": "Connection source",
  "ui.workflow.connectionTarget": "Connection target",
  "ui.workflow.defaultToRidgeRegressionOrLogisticRegressionClassification":
    "Default to Ridge (regression) or Logistic Regression (classification).",
  "ui.workflow.deleteNode": "Delete node",
  "ui.workflow.deleteWorkflow": "Delete workflow",
  "ui.workflow.enabled": "Enabled",
  "ui.workflow.files": "Files",
  "ui.workflow.filesRemaining": "files remaining",
  "ui.workflow.fittedModel": "Fitted model",
  "ui.workflow.fromNode": "From node",
  "ui.workflow.greaterThanValue": "Greater than {value0}. ",
  "ui.workflow.historyCheckedAllowANewRun": "History checked; allow a new run",
  "ui.workflow.historyCouldNotBeLoadedCheckActualDatasetAccess":
    "History could not be loaded. Check actual dataset access.",
  "ui.workflow.inheritOrUseTaskDefault": "Inherit or use task default",
  "ui.workflow.keepEditing": "Keep editing",
  "ui.workflow.lessThanValue": "Less than {value0}.",
  "ui.workflow.loadingWorkflow": "Loading workflow…",
  "ui.workflow.loadingWorkflows": "Loading workflows…",
  "ui.workflow.modelSettingsAsAJsonObjectInvalidValuesBlockValidationAndExecution":
    "Model settings as a JSON object. Invalid values block validation and execution.",
  "ui.workflow.moreParameters": "more parameters",
  "ui.workflow.new": "New",
  "ui.workflow.newWorkflow": "New workflow",
  "ui.workflow.nextCleanupBatch": "Next cleanup batch",
  "ui.workflow.noIssues": "No issues.",
  "ui.workflow.nodePalette": "Node palette",
  "ui.workflow.nodeParameters": "Node parameters",
  "ui.workflow.openedSavedVersionValue": "Opened saved version {value0}.",
  "ui.workflow.outputCleanupPending": "Output cleanup pending",
  "ui.workflow.pendingOutputCleanup": "Pending output cleanup",
  "ui.workflow.previousCleanupBatch": "Previous cleanup batch",
  "ui.workflow.readOnlyRunSnapshotVersion": "Read-only run snapshot · version",
  "ui.workflow.refreshHistory": "Refresh history",
  "ui.workflow.reload": "Reload",
  "ui.workflow.required": "(required)",
  "ui.workflow.resolveTheValidationIssuesBeforeRunning":
    "Resolve the validation issues before running.",
  "ui.workflow.retryOutputCleanup": "Retry output cleanup",
  "ui.workflow.returnToCurrentWorkflow": "Return to current workflow",
  "ui.workflow.runHistory": "Run history",
  "ui.workflow.runsAppearHereAfterTheyAreSaved":
    "Runs appear here after they are saved.",
  "ui.workflow.save": "Save",
  "ui.workflow.saveThisDraftBeforeOpeningAnImmutableRunSnapshot":
    "Save this draft before opening an immutable run snapshot.",
  "ui.workflow.savedResultDetails": "Saved result details",
  "ui.workflow.savedResults": "Saved results ·",
  "ui.workflow.savedRunHistory": "Saved run history",
  "ui.workflow.savedRunResults": "Saved run results",
  "ui.workflow.savedRunValue": "Saved run: {value0}.",
  "ui.workflow.savedVersionValue": "Saved version {value0}",
  "ui.workflow.savedVersionValue.workflowClient": "Saved version {value0}.",
  "ui.workflow.savedWorkflow": "Saved workflow",
  "ui.workflow.selectADatasetToChooseActualColumns":
    "Select a dataset to choose actual columns.",
  "ui.workflow.selectANodeInTheGraphOrListToChooseActualColumnsAndConfigureItsParameters":
    "Select a node in the graph or list to choose actual columns and configure its parameters.",
  "ui.workflow.selectedNode": "Selected node",
  "ui.workflow.sourceFilesAreRetainedLearningHappensInsideTrainingFolds":
    "Source files are retained. Learning happens inside training folds.",
  "ui.workflow.step": "Step",
  "ui.workflow.stepParameters": "Step parameters",
  "ui.workflow.theWorkflowIsDeletedItsRemainingOutputFilesHaveASavedCleanupReceiptAndCanBeRetried":
    "The workflow is deleted. Its remaining output files have a saved cleanup receipt and can be retried.",
  "ui.workflow.thisLegacyRunHasNoReadableGraphSnapshotItsSavedResultsRemainAvailable":
    "This legacy run has no readable graph snapshot. Its saved results remain available.",
  "ui.workflow.toNode": "To node",
  "ui.workflow.useWorkflowDataset": "Use workflow dataset",
  "ui.workflow.validate": "Validate",
  "ui.workflow.workflowDataCouldNotBeLoadedCheckYourAccessAndRetry":
    "Workflow data could not be loaded. Check your access and retry.",
  "ui.workflow.workflowDataset": "Workflow dataset",
  "ui.workflow.workflowGraph": "Workflow graph",
  "ui.workflow.workflowName": "Workflow name",
  "ui.workflow.workflowVersion": "Workflow version",
  "ui.workspace.addRidgeStep": "Add Ridge step",
  "ui.workspace.afterSavingApproveEgressForEachRequestASourceChangeOpensASeparateSession":
    "After saving, approve egress for each request. A source change opens a separate session.",
  "ui.workspace.aiResearchWorkbench": "AI research workbench",
  "ui.workspace.approveExecutionOfTheSavedRevisionOnTheCurrentDataVersion":
    "Approve execution of the saved revision on the current data version",
  "ui.workspace.bindCurrentDataToANewPlan": "Bind current data to a new plan",
  "ui.workspace.buildAPlanFromTheCurrentValidForm":
    "Build a plan from the current valid form",
  "ui.workspace.contextVersion": "· context version",
  "ui.workspace.current": "Current:",
  "ui.workspace.editCopySelectedTemplate": "Edit / copy selected template",
  "ui.workspace.equivalentTableSavedDisplayPointCoordinates":
    "Equivalent table: saved display point coordinates (",
  "ui.workspace.executeSavedRevision": "Execute saved revision",
  "ui.workspace.executionStatus": "Execution status:",
  "ui.workspace.loadingWorkspace": "Loading workspace…",
  "ui.workspace.locateTheActualRun": "Locate the actual Run",
  "ui.workspace.meanDifferenceCiValueValue":
    "Mean-difference CI · {value0}% · {value1}",
  "ui.workspace.originalRowIndex": "Original row index",
  "ui.workspace.pairs": " pairs)",
  "ui.workspace.planName": "Plan name",
  "ui.workspace.plansAndTemplates": "Plans and templates",
  "ui.workspace.prepareANewExecutionRequest": "Prepare a new execution request",
  "ui.workspace.producingActualStatisticalMlRuns":
    ", producing actual statistical / ML Runs.",
  "ui.workspace.projectAiWorkbench": "Project AI workbench",
  "ui.workspace.rebindTemplate": "Rebind template",
  "ui.workspace.request": "Request",
  "ui.workspace.requiredColumnTypesAndSteps": "Required column types and steps",
  "ui.workspace.researchSession": "Research session",
  "ui.workspace.reviewTheSameRequest": "Review the same request",
  "ui.workspace.saveATemplateCopy": "Save a template copy",
  "ui.workspace.saveAsTemplate": "Save as template",
  "ui.workspace.savePlan": "Save plan",
  "ui.workspace.savedQuestions": "Saved questions",
  "ui.workspace.savedResponseForThisTurn": "Saved response for this turn",
  "ui.workspace.savingAndEditingDoNotCreateARunUpTo8OrderedAcyclicStatisticalRidgeStepsAreSupportedEachSavedRevisionRequiresSeparateExecutionApproval":
    "Saving and editing do not create a Run. Up to 8 ordered, acyclic statistical / Ridge steps are supported; each saved revision requires separate execution approval.",
  "ui.workspace.seeTheSourceForBusinessStatus":
    "; see the source for business status.",
  "ui.workspace.selectPlan": "Select plan",
  "ui.workspace.selectTemplate": "Select template",
  "ui.workspace.session": "Session",
  "ui.workspace.sessionInformationAndResearchRules":
    "Session information and research rules",
  "ui.workspace.sessionRevisionValueValueSavedQuestions":
    "Session revision {value0} · {value1} saved questions",
  "ui.workspace.showingValueValueCompletePairsSeedValueNoAdditionalFittingOrReEstimation":
    "Showing {value0}/{value1} complete pairs, seed={value2}. No additional fitting or re-estimation.",
  "ui.workspace.templateName": "Template name",
  "ui.workspace.templateR": "Template r",
  "ui.workspace.templatesSaveConfigurationAndColumnTypesEachUseRequiresRebindingDataAndApprovingANewPlan":
    "Templates save configuration and column types. Each use requires rebinding data and approving a new plan.",
  "ui.workspace.theSourceIsASavedRunNotASampledPreview":
    "The source is a saved Run, not a sampled preview.",
  "ui.workspace.thisSavedRecordCannotProduceAFigure":
    "This saved record cannot produce a figure.",
  "ui.workspace.typedStepsEditableJson": "Typed steps (editable JSON)",
  "ui.workspace.unfinishedStepsAreNotMarkedAsSuccessful":
    "; unfinished steps are not marked as successful.",
  "ui.workspace.validateAndSavePlan": "Validate and save plan",
  "ui.workspace.validateAndSaveTemplateRevision":
    "Validate and save template revision",
  "ui.workspace.valueValueDifferenceValue":
    "[{value0}, {value1}]; difference {value2}",
  "ui.workspace.welchRunValueValueDpiPng":
    "Welch · Run {value0} · {value1} DPI (PNG)",
  "workflow.category.cleaning": "Cleaning",
  "workflow.category.data": "Data",
  "workflow.category.ml": "Machine Learning",
  "workflow.category.output": "Output",
  "workflow.category.statistics": "Statistics",
  "workflow.node.anova.description": "One-way ANOVA across groups.",
  "workflow.node.anova.label": "ANOVA",
  "workflow.node.chart.description": "Render a chart from a result.",
  "workflow.node.chart.label": "Chart",
  "workflow.node.chi_square.description": "Chi-square test of independence.",
  "workflow.node.chi_square.label": "Chi-Square",
  "workflow.node.column_select.description": "Keep only the chosen columns.",
  "workflow.node.column_select.label": "Select Columns",
  "workflow.node.correlation.description":
    "Pearson/Spearman/Kendall correlation.",
  "workflow.node.correlation.label": "Correlation",
  "workflow.node.cross_validation.description": "Cross-validated score.",
  "workflow.node.cross_validation.label": "Cross-Validation",
  "workflow.node.dataset.description":
    "Load a project dataset as the pipeline source.",
  "workflow.node.dataset.label": "Dataset",
  "workflow.node.descriptive.description":
    "Descriptive statistics for selected columns.",
  "workflow.node.descriptive.label": "Descriptive Stats",
  "workflow.node.duplicate_handling.description": "Remove duplicate rows.",
  "workflow.node.duplicate_handling.label": "Drop Duplicates",
  "workflow.node.evaluation.description":
    "Read actual saved holdout/validation evaluation for the connected model.",
  "workflow.node.evaluation.label": "Evaluation",
  "workflow.node.export.description": "Export the workflow outputs.",
  "workflow.node.export.label": "Export",
  "workflow.node.feature_selection.description":
    "Select K best encoded features inside each training fold.",
  "workflow.node.feature_selection.label": "Feature Selection",
  "workflow.node.filter.description":
    "Filter rows by a simple column condition.",
  "workflow.node.filter.label": "Filter Rows",
  "workflow.node.group_comparison.description":
    "Two-group test (Welch/Mann-Whitney).",
  "workflow.node.group_comparison.label": "Group Comparison",
  "workflow.node.interpretation.description": "Feature importance of a model.",
  "workflow.node.interpretation.label": "Interpretation",
  "workflow.node.missing_value.description": "Drop or impute missing values.",
  "workflow.node.missing_value.label": "Handle Missing",
  "workflow.node.model.description": "Train a model.",
  "workflow.node.model.label": "Model",
  "workflow.node.notebook.description": "Export steps as a notebook.",
  "workflow.node.notebook.label": "Notebook",
  "workflow.node.outlier_strategy.description":
    "Winsorize outliers; flag strategy is unavailable.",
  "workflow.node.outlier_strategy.label": "Outlier Strategy",
  "workflow.node.preprocessing.description":
    "Configure imputation and scaling learned inside each training fold.",
  "workflow.node.preprocessing.label": "Preprocessing",
  "workflow.node.regression.description": "Linear/logistic regression.",
  "workflow.node.regression.label": "Regression",
  "workflow.node.report.description": "Compile results into a report section.",
  "workflow.node.report.label": "Report",
  "workflow.node.sample.description": "Randomly sample a fraction of rows.",
  "workflow.node.sample.label": "Sample",
  "workflow.node.split.description":
    "Save actual train/validation/test row allocation for bounded model training.",
  "workflow.node.split.label": "Split",
  "workflow.node.table.description": "Render an input as a table.",
  "workflow.node.table.label": "Table",
  "workflow.node.type_conversion.description":
    "Coerce a column to a target type.",
  "workflow.node.type_conversion.label": "Convert Type",
  "workflow.parameter.alpha": "Alpha",
  "workflow.parameter.alternative": "Alternative",
  "workflow.parameter.categorical": "Categorical",
  "workflow.parameter.chart_type": "Chart Type",
  "workflow.parameter.column": "Column",
  "workflow.parameter.columns": "Columns",
  "workflow.parameter.confidence_level": "Confidence Level",
  "workflow.parameter.dataset_id": "Dataset Id",
  "workflow.parameter.features": "Features",
  "workflow.parameter.format": "Format",
  "workflow.parameter.fraction": "Fraction",
  "workflow.parameter.group": "Group",
  "workflow.parameter.group_variable": "Group Variable",
  "workflow.parameter.handle_outliers": "Handle Outliers",
  "workflow.parameter.impute_categorical": "Impute Categorical",
  "workflow.parameter.impute_numeric": "Impute Numeric",
  "workflow.parameter.include_raw": "Include Raw",
  "workflow.parameter.k": "K",
  "workflow.parameter.keep": "Keep",
  "workflow.parameter.kind": "Kind",
  "workflow.parameter.l1_ratio": "L1 Ratio",
  "workflow.parameter.method": "Method",
  "workflow.parameter.missing_value_policy": "Missing Value Policy",
  "workflow.parameter.model_id": "Model Id",
  "workflow.parameter.model_params": "Model Params",
  "workflow.parameter.n_splits": "N Splits",
  "workflow.parameter.operator": "Operator",
  "workflow.parameter.partition": "Partition",
  "workflow.parameter.predictors": "Predictors",
  "workflow.parameter.row_limit": "Row Limit",
  "workflow.parameter.seed": "Seed",
  "workflow.parameter.split_strategy": "Split Strategy",
  "workflow.parameter.standardize": "Standardize",
  "workflow.parameter.strategy": "Strategy",
  "workflow.parameter.target": "Target",
  "workflow.parameter.task_type": "Task Type",
  "workflow.parameter.test_size": "Test Size",
  "workflow.parameter.time_variable": "Time Variable",
  "workflow.parameter.title": "Title",
  "workflow.parameter.to_type": "To Type",
  "workflow.parameter.val_size": "Val Size",
  "workflow.parameter.value": "Value",
  "workflow.parameter.variable": "Variable",
  "workflow.parameter.variables": "Variables",

  "workflow.enum.eq": "Equals",
  "workflow.enum.ne": "Does not equal",
  "workflow.enum.gt": "Greater than",
  "workflow.enum.lt": "Less than",
  "workflow.enum.drop": "Drop rows with missing values",
  "workflow.enum.mean": "Mean imputation",
  "workflow.enum.median": "Median imputation",
  "workflow.enum.most_frequent": "Most frequent value",
  "workflow.enum.float": "Decimal number",
  "workflow.enum.integer": "Integer",
  "workflow.enum.string": "Text",
  "workflow.enum.any": "Keep any duplicate",
  "workflow.enum.first": "Keep first duplicate",
  "workflow.enum.last": "Keep last duplicate",
  "workflow.enum.none": "Remove all duplicates",
  "workflow.enum.winsorize": "Winsorize",
  "workflow.enum.two_sided": "Two-sided",
  "workflow.enum.less": "Less",
  "workflow.enum.greater": "Greater",
  "workflow.enum.listwise_delete": "Listwise deletion",
  "workflow.enum.pairwise_delete": "Pairwise deletion",
  "workflow.enum.error_on_missing": "Reject missing values",
  "workflow.enum.pearson": "Pearson",
  "workflow.enum.spearman": "Spearman",
  "workflow.enum.kendall": "Kendall",
  "workflow.enum.welch_t": "Welch t-test",
  "workflow.enum.mann_whitney": "Mann–Whitney U",
  "workflow.enum.linear": "Linear regression",
  "workflow.enum.linear_regression": "Linear regression",
  "workflow.enum.logistic": "Logistic regression",
  "workflow.enum.logistic_regression": "Logistic regression",
  "workflow.enum.regression": "Regression",
  "workflow.enum.binary_classification": "Binary classification",
  "workflow.enum.multiclass_classification": "Multiclass classification",
  "workflow.enum.decision_tree": "Decision tree classifier",
  "workflow.enum.random_forest": "Random forest classifier",
  "workflow.enum.gradient_boosting": "Gradient boosting classifier",
  "workflow.enum.knn": "K-nearest neighbors",
  "workflow.enum.svm": "Support vector classifier",
  "workflow.enum.ridge": "Ridge regression",
  "workflow.enum.lasso": "Lasso regression",
  "workflow.enum.elastic_net": "Elastic net regression",
  "workflow.enum.decision_tree_regressor": "Decision tree regressor",
  "workflow.enum.random_forest_regressor": "Random forest regressor",
  "workflow.enum.gradient_boosting_regressor": "Gradient boosting regressor",
  "workflow.enum.__dummy__": "Dummy baseline",
  "workflow.enum.auto": "Automatic",
  "workflow.enum.bar": "Bar chart",
  "workflow.enum.coefficient": "Coefficients",
  "workflow.enum.roc": "ROC curve",
  "workflow.enum.pr": "Precision–recall curve",
  "workflow.enum.calibration": "Calibration",
  "workflow.enum.residuals": "Residuals",
  "workflow.enum.validation": "Validation set",
  "workflow.enum.test": "Test set",
  "workflow.enum.zip": "ZIP archive",
  "workflow.enum.html": "HTML",
  "workflow.enum.csv": "CSV",
  "workflow.enum.svg": "SVG",
  "workflow.enum.ipynb": "Notebook",
  "workflow.enum.json": "JSON",
  "workflow.enum.yes": "Yes",
  "workflow.enum.no": "No",
  "workflow.enum.train_test": "Train / test",
  "workflow.enum.train_val_test": "Train / val / test",
  "workflow.enum.kfold": "K-Fold",
  "workflow.enum.stratified_kfold": "Stratified K-Fold",
  "workflow.enum.group_kfold": "Group K-Fold",
  "workflow.enum.time_series_split": "Time-series split",
  "ml.metric.unverifiedDefinition":
    "Definition and direction were not returned. Check the saved evaluation record.",
  "research.method.descriptive": "Descriptive statistics",
  "research.method.welch_t": "Welch two-group mean comparison",
  "research.method.paired_t": "Paired t-test",
  "research.method.one_way_anova": "One-way ANOVA",
  "research.method.chi_square_independence": "Chi-square independence test",
  "research.method.pearson": "Pearson correlation",
  "research.method.spearman": "Spearman rank correlation",
  "research.method.linear_regression": "OLS linear regression",
  "research.label.variable": "Variable",
  "research.label.group": "Group",
  "research.label.validN": "Valid n",
  "research.label.missingN": "Missing n",
  "research.label.mean": "Mean",
  "research.label.min": "Minimum",
  "research.label.median": "Median",
  "research.label.max": "Maximum",
  "research.label.meanLower": "Mean CI lower bound",
  "research.label.meanUpper": "Mean CI upper bound",
  "research.label.difference": "Mean difference",
  "research.label.differenceLower": "Mean difference CI lower bound",
  "research.label.differenceUpper": "Mean difference CI upper bound",
  "research.label.dfBetween": "Between-group df",
  "research.label.dfWithin": "Within-group df",
  "research.label.completePairsN": "Complete-pair n",
  "research.label.correlationLower": "Correlation CI lower bound",
  "research.label.correlationUpper": "Correlation CI upper bound",
  "research.label.adjustedR2": "Adjusted R²",
  "research.label.modelP": "Model p",
  "research.label.term": "Term",
  "research.label.coefficient": "Coefficient β",
  "research.label.ciLower": "CI lower bound",
  "research.label.ciUpper": "CI upper bound",
  "research.label.reference": "Reference category",
  "research.label.levels": "Encoded levels",
  "research.label.rowCategory": "Row category",
  "research.label.row": "Row {index}",
  "research.table.descriptive": "Saved descriptive statistics",
  "research.table.observed": "Observed counts",
  "research.table.expected": "Expected counts",
  "research.table.coefficients":
    "OLS coefficients · saved confidence level {level}%",
  "research.table.encoding": "Saved category encoding",
  "research.note.perVariable":
    "NaN values were deleted separately for each variable; valid n appears in the table. Overview n is the saved summary, not necessarily a common complete sample.",
  "research.note.meanInterval":
    "Mean intervals come from the saved record. No interval is invented for constants or fewer than two observations. There is no hypothesis-test p-value.",
  "research.note.direction": "Difference direction: {direction}.",
  "research.note.noGroupNames": "The saved record did not provide two names",
  "research.note.welchInterval":
    "Mean difference CI · {level}% · {alternative} · Welch–Satterthwaite. Group mean points are not group mean CIs.",
  "research.note.noInterval":
    "The saved record returned no confidence interval; reopening does not recalculate it.",
  "research.note.paired":
    "The unit is a complete pair; the difference is first minus second variable. The engine returned no paired mean-difference CI, so none is invented.",
  "research.note.anova":
    "The overall F-test does not identify which two groups differ; no post-hoc comparisons or multiplicity adjustments were added. The original engine deleted missing values and group names and returned no group intervals.",
  "research.note.chiSquare":
    "Tests association between categories, not causality. Check saved assumption tests for low expected counts and applicability; no CI was returned.",
  "research.note.fisher": "Saved Fisher z interval · {level}%.",
  "research.note.pearson":
    "Pearson r describes linear association, not causality; the scatterplot adds no fitted line or re-estimation.",
  "research.note.spearman":
    "Spearman ρ uses ranks; ties are retained with the original engine’s tie handling. No new correlation CI or smoothing was added.",
  "research.note.legacyPairwise":
    "For legacy per-variable pairwise deletion with missing values, the table keeps saved values and the scatterplot safely refuses.",
  "research.note.coefficients":
    "Coefficients and intervals are saved OLS results, not Ridge weights. Missing intervals are not replaced with zero or point estimates. Units depend on the original variables; verify unknown units.",
  "research.note.standardize":
    "Saved standardize request: {request}; this OLS engine did not standardize, so coefficients use original variable scales.",
  "research.note.unknown": "Unknown",
  "research.note.rowDiagnostics":
    "Complete row diagnostics remain in the original record; the default single-analysis output includes no residual, Q–Q or observation coordinates.",
  "research.output.single": "Single-analysis output",
  "research.output.status": "Status",
  "research.output.engine": "Engine",
  "research.output.savedResults": "Saved results",
  "research.output.parameters": "Parameters and limitations",
  "research.output.source": "Source",
  "research.output.work": "Work",
  "research.output.data": "Data",
  "research.output.dataSha": "Data SHA256",
  "research.output.snapshot": "Snapshot",
  "research.output.notReturned": "Not returned",
  "research.output.software": "Software versions",
  "research.output.sample":
    "Original n={original} · excluded {excluded} · valid n={valid}",
  "research.output.noSample": "This legacy record returned no sample summary.",
  "research.output.noTemplate":
    "This legacy method is outside the eight templates; provenance is retained and no results are invented.",
  "research.output.noSnapshot": "This legacy work has no figure snapshot",
  "research.output.offline":
    "Self-contained offline output; displayed numbers are rounded and summary CSV retains saved precision. No per-row data or diagnostics by default. A complete research ZIP is not ready; this is not an independent replay package.",
  "research.output.noFigure": "No figure work was saved for this analysis.",
  "research.output.omitScatter":
    "Scatter coordinates are omitted by default because observations can be reconstructed. Saved correlation, sample and limitations follow.",
  "research.output.includeScatter":
    "The user explicitly included saved scatter coordinates locally: {displayed}/{total} observations.",
  "research.output.distribution":
    "Complete distribution summary; the report draws and includes no raw outlier values.",
  "research.output.summaryFigure":
    "The figure uses only saved summaries and available intervals.",
  "research.output.passed": "Passed",
  "research.output.check": "Check required",
  "research.figure.frequency": "Frequency",
  "research.figure.distribution":
    "Full n={n} · {bins} bins · Tukey 1.5 IQR · outliers {displayed}/{total}",
  "research.figure.scatter":
    "Showing {displayed}/{total} pairs · seed={seed} · saved {method} · no new fitting",
  "research.figure.coefficient": "Coefficient β",
  "research.figure.originalScale": "Original variable scale",
  "research.figure.ols": "OLS · n={n} · {interval} · reference β=0",
  "research.figure.savedCi": "Saved CI {level}%",
  "research.figure.pointsOnly": "Coefficient points, no intervals",
  "research.figure.legacyInterval":
    "Mean difference CI · {level}% · {alternative}",
  "research.figure.differenceValue":
    "[{lower}, {upper}]; difference {difference}",
  "research.figure.groupSummary": "{group}: n={n}, mean={mean}, SD={sd}",
  "research.figure.legacyDescription":
    "Saved group means and a separate mean-difference confidence interval; no raw observations.",
  "research.table.aria": "{caption}; horizontally scrollable",
  "chart.aria.axes": "{title}; horizontal axis {x}; vertical axis {y}",
  "chart.aria.table": "{title} values table",
  "chart.axis.falsePositive": "False positive rate",
  "chart.axis.truePositive": "True positive rate",
  "chart.axis.recall": "Recall",
  "chart.axis.precision": "Precision",
  "chart.calibration.top": "Top-label calibration",
  "chart.calibration.positive": "Positive-class probability calibration",
  "chart.axis.predictedProbability": "Mean predicted probability per bin",
  "chart.axis.correctFraction": "Observed accuracy",
  "chart.axis.positiveFraction": "Observed positive fraction",
  "chart.axis.meanResidual": "Mean residual",
  "chart.axis.residualSd": "Residual SD",
  "chart.axis.oddsRatio": "Odds ratio",
  "chart.axis.coefficient": "Coefficient",
  "chart.series.interval": "Saved CI {level}%",
  "chart.series.intervalUnknown": "Saved CI",
  "chart.series.estimate": "Estimate",
  "chart.series.reference": "Reference",
  "chart.axis.fitted": "Fitted value",
  "chart.axis.residual": "Residual",
  "chart.axis.theoretical": "Theoretical quantiles",
  "chart.axis.sample": "Sample quantiles",
  "chart.diagnostics.saved": "Saved diagnostics",
  "statistics.catalog.descriptive.name": "Descriptive statistics",
  "statistics.catalog.descriptive.description":
    "Count, central tendency, dispersion, quartiles, skewness, kurtosis and a confidence interval for the mean of each numeric variable.",
  "statistics.catalog.pearson.name": "Pearson correlation",
  "statistics.catalog.pearson.description":
    "Linear association between two numeric variables (assumes approximate normality).",
  "statistics.catalog.spearman.name": "Spearman rank correlation",
  "statistics.catalog.spearman.description":
    "Monotonic association based on ranks (robust to non-normality and outliers).",
  "statistics.catalog.kendall.name": "Kendall tau correlation",
  "statistics.catalog.kendall.description":
    "Ordinal association based on concordant/discordant pairs; robust for small samples.",
  "statistics.catalog.one_sample_t.name": "One-sample t-test",
  "statistics.catalog.one_sample_t.description":
    "Tests whether a numeric variable's mean differs from a reference value (μ₀).",
  "statistics.catalog.welch_t.name": "Welch two-sample t-test",
  "statistics.catalog.welch_t.description":
    "Compares two independent group means without assuming equal variances (default choice).",
  "statistics.catalog.student_t.name": "Student two-sample t-test",
  "statistics.catalog.student_t.description":
    "Compares two independent group means assuming equal variances (use only when justified).",
  "statistics.catalog.paired_t.name": "Paired t-test",
  "statistics.catalog.paired_t.description":
    "Compares two paired/repeated measurements (provide two aligned numeric variables).",
  "statistics.catalog.mann_whitney.name": "Mann–Whitney U test",
  "statistics.catalog.mann_whitney.description":
    "Non-parametric comparison of two independent groups (ranks; no normality assumption).",
  "statistics.catalog.wilcoxon.name": "Wilcoxon signed-rank test",
  "statistics.catalog.wilcoxon.description":
    "Non-parametric test for two paired measurements (ranks of differences).",
  "statistics.catalog.chi_square_independence.name":
    "Chi-square test of independence",
  "statistics.catalog.chi_square_independence.description":
    "Tests association between two categorical variables via a contingency table.",
  "statistics.catalog.chi_square_gof.name": "Chi-square goodness-of-fit",
  "statistics.catalog.chi_square_gof.description":
    "Tests whether one categorical variable follows specified expected proportions.",
  "statistics.catalog.fisher_exact.name": "Fisher's exact test",
  "statistics.catalog.fisher_exact.description":
    "Exact test of association for a 2×2 table (preferred for small samples).",
  "statistics.catalog.one_way_anova.name": "One-way ANOVA",
  "statistics.catalog.one_way_anova.description":
    "Compares means across ≥2 independent groups (assumes normality & equal variances).",
  "statistics.catalog.welch_anova.name": "Welch ANOVA",
  "statistics.catalog.welch_anova.description":
    "One-way ANOVA robust to unequal variances and unequal group sizes.",
  "statistics.catalog.kruskal_wallis.name": "Kruskal–Wallis H test",
  "statistics.catalog.kruskal_wallis.description":
    "Non-parametric comparison of ≥2 independent groups (rank-based).",
  "statistics.catalog.linear_regression.name": "Linear regression (OLS)",
  "statistics.catalog.linear_regression.description":
    "Ordinary least-squares linear/multiple regression with inferential statistics (coefficients, p-values, CI), R², VIF and residual diagnostics.",
  "statistics.catalog.logistic_regression.name": "Logistic regression",
  "statistics.catalog.logistic_regression.description":
    "Binary logistic regression with odds ratios, confidence intervals, confusion matrix, ROC-AUC and PR-AUC. Target must have exactly two levels.",
  "statistics.catalog.ridge_regression.name": "Ridge regression",
  "statistics.catalog.ridge_regression.description":
    "L2-regularized linear regression for prediction (shrinks coefficients; no p-values).",
  "statistics.catalog.lasso_regression.name": "Lasso regression",
  "statistics.catalog.lasso_regression.description":
    "L1-regularized linear regression for prediction/feature selection (no p-values).",
  "statistics.catalog.elastic_net_regression.name": "Elastic-net regression",
  "statistics.catalog.elastic_net_regression.description":
    "Combined L1+L2 regularized linear regression for prediction (no p-values).",
  "statistics.category.descriptive": "Descriptive",
  "statistics.category.correlation": "Correlation",
  "statistics.category.two_group": "Two-group comparison",
  "statistics.category.categorical": "Categorical",
  "statistics.category.multi_group": "Multi-group comparison",
  "statistics.category.regression": "Regression",
  "statistics.category.prediction_evaluation": "Prediction evaluation",
  "statistics.catalog.evaluate_predictions.name":
    "Evaluate existing regression predictions",
  "statistics.catalog.evaluate_predictions.description":
    "Evaluate stored actual and predicted values with explicit metrics and bound sources.",
  "statistics.catalog.evaluate_binary_predictions.name":
    "Evaluate existing binary predictions",
  "statistics.catalog.evaluate_binary_predictions.description":
    "Evaluate stored 0/1 labels with an explicit positive label and bound sources.",
  "ui.statistics.configureInResearchPlan": "Configure in a research plan",
  "ui.statistics.boundPredictionPlanRequired":
    "Existing prediction evaluation requires bound source fields and metrics. Configure it in a research plan.",
  "ui.displayBranch.data.projectUploadedFiles":
    "Files uploaded to this project.",
  "ui.displayBranch.data.fileProcessingFailed":
    "The file could not be processed. Re-upload it or try a different file.",
  "ui.displayBranch.data.columnNeeds": "column needs",
  "ui.displayBranch.data.columnsNeed": "columns need",
  "ui.displayBranch.ml.proposalIncompatibleColumnsModels":
    "The proposal does not match the current columns or models; the form was not filled in.",
  "ui.displayBranch.ml.proposalReadyForReview":
    "The AI proposal has filled in the current experiment form; training has not started. Review it before running.",
  "ui.displayBranch.ml.foldsTimeOrderRejected":
    "No folds suggestion — time column is not sorted ascending; training would be rejected.",
  "ui.displayBranch.ml.foldsTimeRequired":
    "Select a time variable; unsorted or unverified time order has no folds suggestion.",
  "ui.displayBranch.ml.foldsGroupRequired":
    "Select a group variable; Group K-Fold needs explicit groups.",
  "ui.displayBranch.ml.foldsStatsUnavailable":
    "No trusted folds suggestion — server statistics unavailable or unverified. Preview rows and sampled profiles are never used for this hint.",
  "ui.displayBranch.ml.metricNotReturned": "Metric not returned",
  "ui.displayBranch.ml.protocolSavedSameRun":
    "The shared sample allocation and preprocessing protocol within folds have been saved; this comparison is limited to the same Run.",
  "ui.displayBranch.ml.allocationIdentifierMissing":
    "The sample allocation identifier was not returned; counts and the seed do not prove that models used the same evaluation samples.",
  "ui.displayBranch.ml.protocolVerifiedRanking":
    "The shared evaluation protocol has been verified. Only results with the same protocol and a finite primary metric are ranked; changing the displayed metric does not change the saved ranking.",
  "ui.displayBranch.ml.protocolUnverifiedNoRanking":
    "The evaluation protocol is unverified, so results are not ranked. The saved results lack a shared sample allocation identifier and a complete preprocessing protocol; models are shown in record order within this Run, without comparisons across experiments.",
  "ui.displayBranch.ml.primarySuffix": "(primary)",
  "ui.displayBranch.ml.baselineTrainingMean": "Training target mean",
  "ui.displayBranch.ml.baselineTrainingClassProportions":
    "Training class proportions",
  "ui.displayBranch.ml.completedEvaluationNotReturned":
    "This model has not yet returned completed evaluation results.",
  "ui.displayBranch.common.checkPassed": "Passed",
  "ui.displayBranch.common.checkNotPassed": "Not passed",
  "ui.displayBranch.materials.manualLocation": "Manual location",
  "ui.displayBranch.materials.reviewed": "Reviewed",
  "ui.displayBranch.materials.candidate": "Candidate",
  "ui.displayBranch.materials.noTextLayer": "No text layer",
  "ui.displayBranch.materials.humanReviewed": "Reviewed by a human",
  "ui.displayBranch.materials.candidateOutsideAiScope":
    "Candidate; cannot be included in the AI scope",
  "ui.displayBranch.profiling.linearAssociation":
    "Linear association between numeric columns.",
  "ui.displayBranch.profiling.rankAssociation":
    "Rank-based (monotonic) association, robust to outliers.",
  "ui.displayBranch.report.errorDetailNotRecorded":
    "No error detail was recorded.",
  "ui.displayBranch.report.notPassed": "Not passed",
  "ui.displayBranch.statistics.proposalIncompatibleMethodsColumns":
    "The proposal does not match the current methods or columns; the form was not filled in.",
  "ui.displayBranch.statistics.savedParametersRestored":
    "Saved parameters have been restored; opening and editing do not trigger calculations.",
  "ui.displayBranch.statistics.proposalReadyForReview":
    "The AI proposal has filled in the current form; it has not been executed. Review the parameters before running.",
  "ui.displayBranch.statistics.numericOnlySuffix": ", numeric only",
  "ui.displayBranch.statistics.excludingTargetSuffix": " (besides the target)",
  "ui.displayBranch.statistics.targetRequired":
    "Select a target variable to run.",
  "ui.displayBranch.statistics.groupRequired":
    "Select a grouping variable to run.",
  "ui.displayBranch.statistics.oddsRatios95Ci": "Odds ratios (95% CI)",
  "ui.displayBranch.common.notReturned": "Not returned",
  "ui.displayBranch.statistics.significantAlpha005":
    "Statistically significant at α=0.05; this does not establish causation or practical importance.",
  "ui.displayBranch.statistics.notSignificantAlpha005":
    "Not statistically significant at α=0.05.",
  "ui.displayBranch.statistics.noPValueNoDecision":
    "This method did not return a p-value; no significance decision is made.",
  "ui.displayBranch.statistics.meanDifference": "Mean difference",
  "ui.displayBranch.common.needsVerification": "Needs verification",
  "ui.displayBranch.workflow.chooseColumn": "Choose a column",
  "ui.displayBranch.workflow.confirmDeleteHistory":
    "Delete this workflow and its saved workflow history?",
  "ui.displayBranch.workflow.unsavedDraftChoice":
    "This draft has unsaved edits. Save it first, or discard the edits to continue.",
  "ui.displayBranch.workflow.deleteSavedWorkflow": "Delete saved workflow",
  "ui.displayBranch.workflow.discardEditsContinue":
    "Discard edits and continue",
  "ui.displayBranch.workflow.newDraft": "New draft",
  "ui.displayBranch.download.cancel": "Cancel download",
  "ui.displayBranch.materials.manualLocationNote": "Manual location note",
  "ui.displayBranch.common.unknown": "Unknown",
  "ui.displayBranch.locale.chineseShort": "ZH",
  "ui.displayBranch.preview.project": "Project",
  "ui.displayBranch.preview.fields": "Fields",
  "ui.displayBranch.preview.dataQuality": "Data quality",
  "ui.displayBranch.preview.researchNotesMissing": "No research notes recorded",
  "ui.displayBranch.preview.verifyingSavedEvidence":
    "Verifying saved records, sources and parameters…",
  "ui.displayBranch.preview.unrecordedUnavailable":
    "Not recorded / unavailable",
  "ui.displayBranch.preview.providerUnverifiedSavedProtocol":
    "Protocol configuration saved; the live provider is still unverified.",
  "ui.displayBranch.preview.agentNotReadyManualAvailable":
    "AGENT_NOT_READY · Not configured; manual analysis remains available.",
  "ui.displayBranch.workspace.collapse": "Collapse",
  "ui.displayBranch.workspace.expand": "Expand",
  "ui.displayBranch.project.collapseObjectsTools":
    "Collapse project objects and tools",
  "ui.displayBranch.project.showObjectsTools": "Show project objects and tools",
  "ui.displayBranch.workspace.analysis": "Analysis",
  "ui.displayBranch.workspace.works": "Works",
  "ui.displayBranch.workspace.savedObjects": "Saved objects",
  "ui.displayBranch.workspace.processing": "Processing…",
  "ui.displayBranch.workspace.unsavedEdits": "Unsaved changes",
  "ui.displayBranch.workspace.runNewCalculation": "Run a new calculation",
  "ui.displayBranch.workspace.rebinSaveCopy": "Rebin and save a copy",
  "ui.displayBranch.workspace.saveFigure": "Save figure",
  "ui.displayBranch.workspace.notCalculated": "Not calculated",
  "ui.displayBranch.workspace.inspector": "Inspector",
  "ui.displayBranch.workspace.notSelected": "Not selected",
  "ui.displayBranch.workspace.fullSampleOutlierCount":
    "Full n / displayed outlier count",
  "ui.displayBranch.workspace.effectiveSampleCoefficientCount":
    "Valid observations n / coefficient term count",
  "ui.displayBranch.workspace.completePairsDisplayedPoints":
    "Complete pairs n / displayed point count",
  "ui.displayBranch.common.notApplicable": "Not applicable",
  "ui.displayBranch.materials.videoLocationCandidate":
    "The video location is a candidate; check the actual duration during playback.",
  "ui.displayBranch.figure.verifyRunGroupsIntervals":
    "Check the Run, group descriptions and intervals.",
  "ui.displayBranch.figure.groupPointsDifferenceIntervals":
    "Group mean points have no group mean CI; the interval below is the CI for the difference between the two group means.",
  "ui.displayBranch.figure.fullDistributionCappedOutliers":
    "Distribution of the complete valid sample; displayed outliers are capped, and summaries use the full sample.",
  "ui.displayBranch.figure.savedOlsIntervals":
    "Saved OLS coefficients and available intervals; see the table for categorical reference groups. Units follow the original variable scales.",
  "ui.displayBranch.common.pendingVerification": "Pending verification",
  "ui.displayBranch.workspace.saveSessionInfo": "Save session information",
  "ui.displayBranch.workspace.createResearchSession": "Create research session",
  "ui.displayBranch.workspace.continuousSessionMissing":
    "No continuous session has been created yet",
  "quality.category.structure": "Structure",
  "quality.category.completeness": "Completeness",
  "quality.category.uniqueness": "Uniqueness",
  "quality.category.validity": "Validity",
  "quality.category.consistency": "Consistency",
  "quality.category.temporal": "Temporal",
  "quality.category.distribution": "Distribution",
  "quality.category.domain": "Domain",
  "quality.emptyValue": "(empty)",
  "chart.regression.oddsTitle": "Odds ratios · saved CI {level}%",
  "chart.regression.coefficientTitle": "Coefficients · saved CI {level}%",
  "chart.regression.oddsWithoutLevel": "Odds ratios · saved intervals",
  "chart.regression.coefficientWithoutLevel": "Coefficients · saved intervals",
  "quality.pagination": "Page {page} of {pages} · {count} findings",
  "statistics.variables.count": "Variables ({count})",
  "statistics.variables.range": "{min}–{max}, numeric only",
  "statistics.variables.numeric": "Numeric only",
  "statistics.variables.besidesTarget":
    "Numeric predictors, excluding the target",
  "statistics.defaultExperiment": "{task} experiment",
  "statistics.variables.title": "Variables",
  "statistics.variables.select": "Select {range}",
  "statistics.variables.selectNumeric": "Select {range}, numeric only",
  "statistics.variables.none": "No eligible columns are available.",
  "statistics.variables.noneNumeric":
    "No eligible numeric columns are available.",
  "statistics.variables.noneBesidesTarget":
    "No eligible numeric predictors are available after excluding the target.",
  "diagnostic.original": "Original saved diagnostic",
  "locale.switchToEnglish": "Switch to English",
  "locale.switchToChinese": "Switch to Simplified Chinese",
  "auth.checkingSession": "Checking session…",
  "workflow.defaultName": "Analysis workflow",
  "workflow.deletedPending":
    "Workflow deleted. Some output files still need cleanup; retry them below.",
  "workflow.deletedCompleted":
    "Workflow and its output files deleted. Data versions and separate ML runs are retained.",
  "workflow.cleanupCompleted": "Output cleanup completed.",
  "workflow.cleanupPending":
    "Some output files still need cleanup. The saved receipt remains available.",
  "workflow.validationReady": "The workflow is ready to run.",
  "workflow.validationIssues":
    "Resolve the issues listed beside the graph before running.",
  "workflow.unsavedEdits": " · Unsaved edits",
  "workflow.legacyParameters": " · Legacy parameters remain readable",
  "workflow.legacyVersion": "Legacy",
  "workflow.downloadModel": "Download fitted model",
  "workflow.downloadArtifact": "Download {filename}",
  "workflow.includesRawRows": " · Includes explicitly selected source rows",
  "workflow.parameter.greaterThan": "Greater than {bound}.",
  "workflow.parameter.lessThan": "Less than {bound}.",
  "workflow.status.cancelled": "Cancelled",
  "workflow.status.cleanupPending": "Cleanup pending",
  "workflow.parameterDescription.model.model_id":
    "Default to Ridge (regression) or Logistic Regression (classification).",
  "workflow.parameterDescription.cross_validation.model_id":
    "Default to Ridge (regression) or Logistic Regression (classification).",
  "toast.notifications": "Notifications",
  "toast.close": "Close notification",
  "download.cancel": "Cancel download",
  "download.saving": "Saving file…",
  "workflow.aria.node":
    "Press Enter or Space to select a step; arrow keys move it. Backspace deletes the selection and Escape cancels it.",
  "workflow.aria.nodeLocked":
    "This saved snapshot is read-only. Press Enter or Space to select a step and Escape to cancel the selection.",
  "workflow.aria.edge":
    "Press Enter or Space to select a connection, Backspace to delete it, or Escape to cancel the selection.",
  "workflow.aria.edgeLocked":
    "This saved snapshot is read-only. Press Enter or Space to select a connection and Escape to cancel the selection.",
  "workflow.aria.moved":
    "Moved selected step {direction}. Position: x {x}, y {y}.",
  "workflow.aria.left": "left",
  "workflow.aria.right": "right",
  "workflow.aria.up": "up",
  "workflow.aria.down": "down",
  "workflow.aria.controls": "Graph controls",
  "workflow.aria.zoomIn": "Zoom in",
  "workflow.aria.zoomOut": "Zoom out",
  "workflow.aria.fitView": "Fit graph to view",
  "workflow.aria.interactive": "Toggle graph interaction",
  "workflow.aria.minimap": "Graph overview",
  "workflow.aria.handle": "Connection port",
  "app.title": "Quantelune — AI-Assisted Statistics & ML Workbench",
  "app.description":
    "An open-source visual research workbench for reproducible applied statistics and machine learning, with AI assistance.",
  "diagnostic.code": "Diagnostic code: {code}.",
  "quality.validation.number": "{label} must be a number.",
  "quality.validation.ratio": "{label} must be between 0 and 1.",
  "quality.rulesUnavailable":
    "This run completed with {count} rule errors; their results are unavailable.",
  "download.saveAnalysis": "Save Quantelune single-analysis output",
  "quality.validation.columns": "Max columns must be a positive whole number.",
  "validation.nameLength": "Name must be at most 80 characters",
  "validation.email": "Enter a valid email address",
  "validation.passwordLength": "Password must be at least 8 characters",
  "validation.passwordRequired": "Password is required",
  "validation.confirmPassword": "Confirm your password",
  "validation.passwordMismatch": "Passwords do not match",
  "validation.titleRequired": "Title is required",
  "validation.titleLength": "Title must be at most 255 characters",
  "validation.descriptionLength": "Description must be at most 2000 characters",
  "validation.researchQuestionLength":
    "Research question must be at most 2000 characters",
  "chart.tooltip.missing": "{name}<br/>missing: <b>{value}%</b>",
  "chart.heatmap.description":
    "Returned correlations for {count} columns. See the equivalent table for numeric values.",
  "chart.missing.description":
    "Returned missing rates for {count} displayed columns. See the equivalent table for numeric values.",
  "data.notYetValidated": "Not yet validated",
  "workspace.view.table": "Table",
  "workspace.view.figure": "Figure",
  "workspace.view.diagnostics": "Diagnostics",
  "workspace.view.record": "Record / report",
  "workspace.panel.parameters": "Parameters",
  "workspace.panel.style": "Style",
  "workspace.panel.sources": "Sources",
  "workspace.panel.ai": "AI assistant",
  "workspace.copyTitle": "{title} copy",
  "workspace.templateTitle": "{title} · template",
  "workspace.figureTitle": "{title} · figure",
  "workspace.defaultPlanTitle": "Research plan",
  "workspace.savedObject": "Saved object",
  "project.defaultForkTitle": "Fork",
  "workspace.runLabel": "Run",
  "preview.disclosure.project": "Project title and research description",
  "preview.disclosure.schema": "Selected column names and types",
  "preview.disclosure.quality": "Row and missing-value counts",
  "preview.disclosure.qualityML":
    "Row and missing-value counts and current ML preflight counts (nonempty targets, class count/minimum class size, groups and trust status; this form must also be approved)",
  "preview.disclosure.results": "Actual Run references and aggregate results",
  "preview.disclosure.form": "Current valid statistics / ML form parameters",
  "preview.disclosure.materials":
    "Confirmed material excerpts and manual location notes",
  "preview.disclosure.history":
    "Questions and saved field facts whose access has been rechecked in this session",
  "preview.disclosure.rules":
    "Saved confirmations, preferences, project rules and task requirements bound to the current source",
  "ui.residual.common.creating": "Creating…",
  "ui.residual.quality.nearConstantDominantShare":
    "Dominant-value share that flags near-constant columns",
  "ui.residual.data.supportedDropFormatsLimit":
    "Drag a CSV, XLSX or Parquet file onto the bench — up to 100 MB.",
  "ui.residual.quality.failMissingRate": "Fail columns above this missing rate",
  "ui.residual.quality.outlierThreshold":
    "Flag columns with more outliers than this",
  "ui.residual.quality.duplicateShareThreshold":
    "Flag duplicate rows above this share",
  "ui.residual.statistics.inferenceTestsModels": "Inference, tests, models",
  "ui.residual.quality.maxColumnsPositiveInteger":
    "Max columns must be a positive whole number.",
  "ui.residual.ml.outlierDiscovery": "Outlier discovery",
  "ui.residual.quality.highCardinalityUniqueShare":
    "Unique-value share that flags high cardinality",
  "ui.residual.ml.unsupervisedGrouping": "Unsupervised grouping",
  "ui.residual.quality.warnMissingRate": "Warn columns above this missing rate",
  "ui.residual.quality.ohlcv": "ohlcv",
  "ui.residual.common.stale": "stale",
  "ui.residual.research.runSuffix": "· Run",
  "ui.residual.research.figureSuffix": "· Figure",
  "ui.residual.research.templateSuffix": "· Template",
  "ui.residual.materials.uploadIncompleteSupportLimits":
    "Upload incomplete. Supports UTF-8 text, PDFs with a text layer, PNG/JPEG/GIF/WebP, WAV and MP4; materials: 10MiB, PDFs: 4MiB. No OCR or transcription.",
  "ui.residual.workspace.sessionNotSaved":
    "Session not saved. Check the source and revision; local changes are retained.",
  "ui.residual.workspace.sessionSourceUnavailable":
    "The session source is unavailable. Create a new session bound to the current source.",
  "ui.residual.workspace.savedListUnavailable":
    "The saved list is unavailable. Check the current account and project permissions.",
  "ui.residual.workspace.saveFailedRecordRetained":
    "Save failed. Check the current permissions, data version and revision; the previous record was not overwritten.",
  "ui.residual.workspace.savedStateUnavailableEmpty":
    "The saved state is unavailable; the workspace has returned to an empty state.",
  "ui.residual.research.savedPointsRowOrder":
    "Saved display points; row identities are counted in the original data order",
  "ui.residual.workspace.savedRecordSourceUnavailable":
    "The saved record is unavailable or its source has changed.",
  "ui.residual.preview.fixStepJsonBeforeRidge":
    "Fix the step JSON before adding Ridge; all fields are validated on save.",
  "ui.residual.workspace.analysisCreationFailed":
    "Analysis creation failed. Select accessible, verified data.",
  "ui.residual.common.copy": "Copy",
  "ui.residual.preview.securityConfigurationUnavailable":
    "Security configuration is temporarily unavailable",
  "ui.residual.workspace.objectSourceFallback":
    "Object or source unavailable; a safe fallback has been applied. Reopen an object you are authorized to access.",
  "ui.residual.workspace.stateNotSavedObjectsRetained":
    "Workspace state not saved. Reopen it to check the revision; existing objects are retained.",
  "ui.residual.materials.referenceNotSavedRereview":
    "Reference not saved. Check the source version, page number, code point range, region or time boundaries; changes to the reference require a new review.",
  "ui.residual.preview.contextUnavailable":
    "The current context is unavailable. Select an object you are authorized to access again.",
  "ui.residual.materials.currentReferenceUnavailable":
    "The current material reference is unavailable.",
  "ui.residual.materials.playbackUseNativeControls":
    "Playback did not succeed. Use the native playback controls.",
  "ui.residual.workspace.operationIncompleteCheckSavedState":
    "Operation incomplete. Check the current source, field types and revision; read the saved state for unconfirmed calculations.",
  "ui.residual.materials.listUnavailable": "The material list is unavailable.",
  "ui.residual.materials.referenceMetadataUnavailable":
    "The material reference's version, selection, review or length is unavailable. Check it again and choose a short reference.",
  "ui.residual.materials.fileVersionUnavailable":
    "The material or original file version is unavailable.",
  "ui.residual.materials.viewerUnavailable":
    "The material viewer is unavailable. Check permissions, version or file format.",
  "ui.residual.workspace.savedSourceChangedNoRerun":
    "Source bytes, version or permissions have changed; reading saved content is refused, and analysis will not rerun.",
  "ui.residual.workspace.templateUnavailable":
    "The template is unavailable. Select a record you are authorized to access.",
  "ui.residual.workspace.templateNotSavedResultsRetained":
    "Template not saved. Check the steps, column types and revision; the original template and previous results are retained.",
  "ui.residual.materials.durationUnavailableCannotSeek":
    "The browser has not obtained a usable duration yet; seeking is unavailable.",
  "ui.residual.research.figureNotSaved": "Research figure not saved.",
  "ui.residual.workspace.focusWork": "Focus work",
  "ui.residual.materials.videoPlaybackFailed": "The video could not be played.",
  "ui.residual.preview.planListUnavailable": "The plan list is unavailable.",
  "ui.residual.preview.callIncompleteNoDuplicateSuccess":
    "The call did not complete. Check the configuration, approval or saved state; the same request will not resubmit a successful analysis.",
  "ui.residual.workspace.runOrDraftIncompleteNewCalculation":
    "The run or draft save did not complete. Check run history; clicking again creates a new explicit calculation.",
  "ui.residual.workspace.exitWorkFocus": "Exit work focus",
  "ui.residual.preview.configurationNotSavedCredentialService":
    "Configuration not saved. Check the input and Windows secure credential service.",
  "ui.residual.materials.audioPlaybackFailed": "The audio could not be played.",
  "ui.residual.preview.savedNotRedisplayed": "(saved; not shown)",
  "quality.rule.EMPTY_DATASET.name": "Empty dataset",
  "quality.rule.EMPTY_DATASET.description":
    "Flags a dataset that has zero rows or zero columns.",
  "quality.rule.DUPLICATE_ROWS.name": "Duplicate rows",
  "quality.rule.DUPLICATE_ROWS.description":
    "Flags when the proportion of fully duplicated rows exceeds a threshold. Duplicates may be legitimate (e.g. repeated measurements) — review before removing.",
  "quality.rule.TOO_MANY_COLUMNS.name": "Too many columns",
  "quality.rule.TOO_MANY_COLUMNS.description":
    "Flags datasets wider than a configurable column limit.",
  "quality.rule.HIGH_OVERALL_MISSINGNESS.name": "High overall missingness",
  "quality.rule.HIGH_OVERALL_MISSINGNESS.description":
    "Flags when the dataset-wide missing-value rate is high.",
  "quality.rule.EMPTY_COLUMN.name": "Empty column",
  "quality.rule.EMPTY_COLUMN.description":
    "Flags a column whose values are all null.",
  "quality.rule.HIGH_MISSINGNESS.name": "High column missingness",
  "quality.rule.HIGH_MISSINGNESS.description":
    "Flags columns whose missing rate crosses warning/high thresholds.",
  "quality.rule.CONSTANT_COLUMN.name": "Constant column",
  "quality.rule.CONSTANT_COLUMN.description":
    "Flags a column with a single distinct non-null value (no information).",
  "quality.rule.NEAR_CONSTANT_COLUMN.name": "Near-constant column",
  "quality.rule.NEAR_CONSTANT_COLUMN.description":
    "Flags columns dominated by a single value above a frequency threshold.",
  "quality.rule.HIGH_CARDINALITY.name": "High cardinality",
  "quality.rule.HIGH_CARDINALITY.description":
    "Flags text/categorical columns with a very high unique-value ratio.",
  "quality.rule.POSSIBLE_IDENTIFIER.name": "Possible identifier",
  "quality.rule.POSSIBLE_IDENTIFIER.description":
    "Flags columns that are almost entirely unique (likely an ID/key).",
  "quality.rule.MIXED_TYPES.name": "Mixed types",
  "quality.rule.MIXED_TYPES.description":
    "Flags string columns that mix numeric-looking and non-numeric values, which often indicates a parsing or data-entry problem.",
  "quality.rule.NUMERIC_OUTLIERS.name": "Numeric outliers",
  "quality.rule.NUMERIC_OUTLIERS.description":
    "Flags numeric columns with a high IQR-based outlier rate. Outliers are a prompt to review — not automatically errors.",
  "quality.rule.NON_FINITE_NUMERIC.name": "Non-finite numeric values",
  "quality.rule.NON_FINITE_NUMERIC.description":
    "Flags numeric columns containing NaN, +Inf or -Inf.",
  "quality.rule.INVALID_RANGE.name": "Out-of-range numeric values",
  "quality.rule.INVALID_RANGE.description":
    "Flags numeric columns with values outside an explicit configured [numeric_min, numeric_max] range. Disabled unless bounds are configured.",
  "quality.rule.INVALID_DATETIME.name": "Invalid datetime values",
  "quality.rule.INVALID_DATETIME.description":
    "Flags string columns that look date-like but contain values that fail date parsing.",
  "quality.rule.DUPLICATE_TIMESTAMPS.name": "Duplicate timestamps",
  "quality.rule.DUPLICATE_TIMESTAMPS.description":
    "Flags datetime columns with repeated timestamp values.",
  "quality.rule.NON_MONOTONIC_TIME.name": "Non-monotonic time series",
  "quality.rule.NON_MONOTONIC_TIME.description":
    "Flags datetime columns that are not sorted, when the data may be expected to be time-ordered. This is a hint, not an error.",
  "quality.rule.IRREGULAR_TIME_INTERVAL.name": "Irregular time intervals",
  "quality.rule.IRREGULAR_TIME_INTERVAL.description":
    "Flags datetime columns where the gaps between consecutive (sorted) timestamps are inconsistent — a sign of missing periods.",
  "quality.rule.OHLC_CONSISTENCY.name": "OHLC consistency",
  "quality.rule.OHLC_CONSISTENCY.description":
    "Checks high ≥ low, high ≥ open/close and low ≤ open/close for each row.",
  "quality.rule.NON_POSITIVE_PRICE.name": "Non-positive price",
  "quality.rule.NON_POSITIVE_PRICE.description":
    "Flags OHLC price values that are ≤ 0.",
  "quality.rule.NEGATIVE_VOLUME.name": "Negative volume",
  "quality.rule.NEGATIVE_VOLUME.description": "Flags negative volume values.",
  "quality.rule.DUPLICATE_MARKET_TIMESTAMP.name": "Duplicate market timestamp",
  "quality.rule.DUPLICATE_MARKET_TIMESTAMP.description":
    "Flags repeated timestamps (same period recorded more than once).",
  "quality.rule.MISSING_TIME_PERIODS.name": "Missing time periods",
  "quality.rule.MISSING_TIME_PERIODS.description":
    "For daily data, flags gaps larger than one day between consecutive timestamps. Makes no weekend/holiday assumptions (crypto trades 24/7).",
  "quality.rule.EXTREME_RETURN.name": "Extreme returns",
  "quality.rule.EXTREME_RETURN.description":
    "Flags period-over-period returns whose magnitude exceeds a threshold. Informational — large moves can be genuine.",
  "quality.rule.ZERO_VOLUME.name": "Zero-volume streak",
  "quality.rule.ZERO_VOLUME.description":
    "Flags consecutive zero-volume bars beyond a configured streak length.",
  "preview.statisticLabel": "Statistic",
  "common.week": "Week {count}",
  "quality.dimension.usability": "Usability",
  "data.table.format": "Format",
  "data.fileCount.one":
    "{count} file · Upload CSV, Excel or Parquet, up to 100 MB",
  "data.fileCount.other":
    "{count} files · Upload CSV, Excel or Parquet, up to 100 MB",
  "preview.context.routeReset":
    "Choose objects again after changing pages; old column and result references were cleared.",
  "preview.context.objectReset":
    "Previous object context was cleared. Select it again.",
  "preview.context.file": "File: {name}",
  "preview.context.projectFiles": "Project files",
  "preview.context.selectedColumns":
    "Only the selected column types and bounded summaries are published; preview rows stay local.",
  "preview.context.chooseColumns":
    "File selected. Choose the columns for AI context.",
  "preview.context.chooseFile":
    "Choose the current file and columns to establish context.",
  "preview.context.statisticsComplete":
    "The current valid form parameters can be approved separately for egress.",
  "preview.context.statisticsIncomplete":
    "The form is incomplete. Only project, selected columns or real result context is available.",
  "preview.context.mlComplete":
    "The current valid experiment configuration can be approved separately for egress; full-data preflight remains required before execution.",
  "preview.context.mlIncomplete":
    "The experiment configuration is incomplete. Choose a target, features and models; source references remain separate.",
  "preview.context.materials":
    "Extracted text is a candidate. Confirm citations manually, then approve each egress request in the panel. This page creates no measurement columns.",
  "preview.context.savedResults": "Saved results",
  "preview.context.savedRun":
    "The interpretation source is a saved real Run. No new analysis has run.",
  "preview.context.chooseSavedRun":
    "Choose the current dataset and saved record; old result references were cleared.",
  "entry.shortcut": "Ctrl + Enter",
  "entry.chooseVariables":
    "Select 1–50 relevant variables under Reference variables before starting.",
  "entry.home": "Research home",
  "entry.title": "Which dataset shall we start with today?",
  "entry.subtitle": "Choose data and the question you want to answer.",
  "entry.project": "Project",
  "entry.chooseProject": "Choose a project",
  "entry.existingProjects": "Your projects",
  "entry.new": "New",
  "entry.continueHint": "Continue your research, or choose different data.",
  "entry.searchProjects": "Find a project",
  "entry.loadingProjects": "Loading projects…",
  "entry.selectToStart": "Choose a project, then select or upload data.",
  "entry.createToStart":
    "Create a project to keep your data and analyses together.",
  "entry.noSearchResults": "No matching projects. Try another search.",
  "entry.noProjects": "No projects yet. Create one to upload data.",
  "entry.continueProject": "Continue research",
  "entry.useProjectData": "Use data from this project",
  "entry.dataset": "Dataset",
  "entry.chooseDataset": "Choose a dataset",
  "entry.loadingData": "Loading data source…",
  "entry.chooseReadyData":
    "Choose ready data. Files being validated are unavailable for discussion.",
  "entry.noData": "This project has no data yet. Upload it here.",
  "entry.manual": "Open project for manual analysis",
  "entry.shape": "{rows} rows · {columns} columns",
  "entry.source": "View data source",
  "entry.question": "Research question",
  "entry.questionHint":
    "Which variables, differences or relationships would you like to explore?",
  "entry.variables": "Reference variables",
  "entry.sourceUnavailable":
    "The data source is unavailable. Choose again or refresh.",
  "entry.startDiscussion": "Start discussion",
  "entry.discussionApproval": "Confirm this discussion",
  "entry.previewScope":
    "This discussion uses your selected project and data. Review the source and egress before sending. Continue proposals in the project workspace.",
  "entry.appearance": "Appearance",
  "entry.mode": "Color mode",
  "entry.mode.light": "Light",
  "entry.mode.dark": "Dark",
  "entry.mode.system": "System",
  "entry.preset": "Color preset",
  "entry.preset.forest": "Forest and sage",
  "entry.preset.ink-sage": "Ink and steel blue",
  "entry.preset.teal-ochre": "Teal and muted cyan",
  "entry.preset.plum-apricot": "Ink plum and apricot",
  "entry.preset.research-blue": "Research blue",
  "entry.preset.neutral": "Quiet neutral",
  "entry.accent": "Accent",
  "entry.accent.preset": "Use preset",
  "entry.accent.forest": "Forest green",
  "entry.accent.blue": "Research blue",
  "entry.accent.plum": "Plum",
  "entry.restoreAppearance": "Restore default appearance",
  "entry.preferenceUnsaved":
    "Appearance applied, but could not be saved on this device.",
  "entry.settings": "Settings",
  "entry.skip": "Skip to research content",
  "entry.expandNav": "Expand navigation",
  "entry.collapseNav": "Collapse navigation",
} as const;
