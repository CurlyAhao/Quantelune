/** Stable messages shared by the locale and error boundaries. */
export const coreEn = {
  "desktop.startupTitle": "Starting your local workbench",
  "desktop.startupWaiting":
    "Starting the analysis service on this computer. Please wait.",
  "desktop.disconnected": "Connection to the local service was interrupted",
  "desktop.reconnectHelp": "Reconnect to return to the workspace.",
  "desktop.reconnect": "Reconnect",
  "desktop.startupFailed": "The local analysis service could not start",
  "desktop.startupRecovery":
    "Close Quantelune and open it again. An email address or password will not resolve this local startup problem.",
  "desktop.startupDetails": "Startup diagnostics",
  "desktop.startupDiagnosticFile": "Local app logs: {name}",
  "desktop.startupDiagnosticMissing":
    "Startup diagnostics could not be saved completely.",
  "common.unavailable": "Unavailable",
  "common.retry": "Retry",
  "common.loading": "Loading…",
  "locale.preferenceNotSaved":
    "Your language choice applies to this session only.",
  "error.unexpected.title": "Something went wrong",
  "error.unexpected.message": "An unexpected error occurred. Please try again.",
  "error.requestFailed.title": "Request failed ({code})",
  "error.requestFailed.message":
    "The request could not be completed. Diagnostic code: {code}.",
  "error.http401.title": "Sign-in required",
  "error.http401.message": "Please sign in again to continue.",
  "error.http403.title": "Access denied",
  "error.http403.message": "You do not have permission to perform this action.",
  "error.http404.title": "Not found",
  "error.http404.message":
    "The requested resource is unavailable or has been deleted.",
  "error.http413.title": "File too large",
  "error.http413.message":
    "The upload exceeds the allowed size. Choose a smaller file.",
  "error.http422.title": "Invalid request",
  "error.http422.message": "Check the supplied values and try again.",
  "error.http500.title": "Server error",
  "error.http500.message":
    "The server could not complete the request. Please try again.",
  "error.offline.title": "Connection problem",
  "error.plotEditor.title": "Could not open the plot script",
  "error.plotEditor.unavailable": "Check that the selected editor can start, or choose another editor.",
  "error.plotEditor.file": "The plot script could not be read. Check that the app data folder is accessible and open the IDE again.",
  "error.plotEditor.changed": "The script has changed. Reload the local code and open the IDE again.",
  "error.plotEditor.runtime": "The local service is unavailable. Reopen Quantelune and try again.",
  "error.offline.message":
    "Cannot reach the Quantelune API. Check the connection and retry.",
  "error.versionConflict.title": "Workflow changed",
  "error.versionConflict.message":
    "This workflow changed in another session. Your draft is kept here; reload the saved version before retrying.",
  "error.publicationUnknown.title": "Publication outcome unknown",
  "error.publicationUnknown.message":
    "Publication outcome is unknown. Review saved run history before starting another run.",
} as const;
