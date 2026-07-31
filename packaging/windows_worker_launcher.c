/*
 * Native trampoline for the private Windows analysis worker.
 *
 * A PyInstaller GUI sets its own DLL search directory. Windows inherits that
 * process-level loader state before a child PyInstaller bootloader runs, which
 * can make MediaPipe resolve GUI runtime DLLs. This static-CRT executable has
 * no Python or ML dependencies. It clears the inherited search state and then
 * starts the real worker with the same standard handles and exit status.
 */

#define _WIN32_WINNT 0x0A00
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <strsafe.h>
#include <wchar.h>

#define LAUNCHER_NAME L"MultiSOCIAL-Worker-Launcher.exe"
#define WORKER_PYTHON L"python.exe"
#define WORKER_SCRIPT L"app\\analysis_worker.py"
#define CLEAN_BOOTSTRAP_ARGUMENT L"--multisocial-worker-clean-bootstrap"

static void reset_pyinstaller_environment(void) {
    const WCHAR *const names[] = {
        L"PYINSTALLER_RESET_ENVIRONMENT",
        L"_PYI_APPLICATION_HOME_DIR",
        L"_PYI_ARCHIVE_FILE",
        L"_PYI_PARENT_PROCESS_LEVEL",
        L"_PYI_SPLASH_IPC",
        L"_MEIPASS2",
    };
    size_t index;

    /* The GUI parent is a separate PyInstaller application.  This static
       launcher must not pass its private bootloader state to the worker. */
    for (index = 0; index < ARRAYSIZE(names); ++index) {
        SetEnvironmentVariableW(names[index], NULL);
    }
}

static int fail(DWORD error) {
    WCHAR message[128];
    HANDLE standard_error;
    DWORD written;

    StringCchPrintfW(message, ARRAYSIZE(message), L"Worker launcher failed (%lu)\n", error);
    standard_error = GetStdHandle(STD_ERROR_HANDLE);
    if (standard_error != INVALID_HANDLE_VALUE && standard_error != NULL) {
        WriteFile(
            standard_error,
            message,
            (DWORD)(wcslen(message) * sizeof(WCHAR)),
            &written,
            NULL
        );
    }
    return 1;
}

static int wait_for_child(PROCESS_INFORMATION *process_info) {
    DWORD exit_code = 1;

    CloseHandle(process_info->hThread);
    WaitForSingleObject(process_info->hProcess, INFINITE);
    if (!GetExitCodeProcess(process_info->hProcess, &exit_code)) {
        exit_code = 1;
    }
    CloseHandle(process_info->hProcess);
    return (int)exit_code;
}

static BOOL is_clean_bootstrap(void) {
    return wcsstr(GetCommandLineW(), CLEAN_BOOTSTRAP_ARGUMENT) != NULL;
}

int wmain(void) {
    WCHAR launcher_path[MAX_PATH];
    WCHAR launcher_executable[MAX_PATH];
    WCHAR worker_python[MAX_PATH];
    WCHAR worker_script[MAX_PATH];
    WCHAR command_line[MAX_PATH * 2 + 64];
    WCHAR *separator;
    STARTUPINFOW startup_info;
    PROCESS_INFORMATION process_info;

    if (!GetModuleFileNameW(NULL, launcher_path, ARRAYSIZE(launcher_path))) {
        return fail(GetLastError());
    }
    separator = wcsrchr(launcher_path, L'\\');
    if (separator == NULL) {
        return fail(ERROR_BAD_PATHNAME);
    }
    *separator = L'\0';
    if (StringCchPrintfW(
            launcher_executable,
            ARRAYSIZE(launcher_executable),
            L"%s\\%s",
            launcher_path,
            LAUNCHER_NAME
        ) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }
    if (StringCchPrintfW(worker_python, ARRAYSIZE(worker_python), L"%s\\%s", launcher_path, WORKER_PYTHON) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }
    if (StringCchPrintfW(worker_script, ARRAYSIZE(worker_script), L"%s\\%s", launcher_path, WORKER_SCRIPT) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }
    if (GetFileAttributesW(worker_python) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(worker_script) == INVALID_FILE_ATTRIBUTES) {
        return fail(GetLastError());
    }

    /* Reset both legacy and modern process DLL-directory mechanisms. */
    SetDllDirectoryW(NULL);
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    reset_pyinstaller_environment();
    if (!SetCurrentDirectoryW(launcher_path)) {
        return fail(GetLastError());
    }

    ZeroMemory(&startup_info, sizeof(startup_info));
    startup_info.cb = sizeof(startup_info);
    startup_info.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup_info.wShowWindow = SW_HIDE;
    startup_info.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup_info.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup_info.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    ZeroMemory(&process_info, sizeof(process_info));
    if (!is_clean_bootstrap()) {
        if (StringCchPrintfW(
                command_line,
                ARRAYSIZE(command_line),
                L"\"%s\" %s",
                launcher_executable,
                CLEAN_BOOTSTRAP_ARGUMENT
            ) != S_OK) {
            return fail(ERROR_BUFFER_OVERFLOW);
        }
        if (!CreateProcessW(
                launcher_executable,
                command_line,
                NULL,
                NULL,
                TRUE,
                CREATE_NEW_PROCESS_GROUP,
                NULL,
                launcher_path,
                &startup_info,
                &process_info)) {
            return fail(GetLastError());
        }
        return wait_for_child(&process_info);
    }
    if (StringCchPrintfW(command_line, ARRAYSIZE(command_line), L"\"%s\" -I \"%s\"", worker_python, worker_script) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }
    if (!CreateProcessW(
            worker_python,
            command_line,
            NULL,
            NULL,
            TRUE,
            0,
            NULL,
            launcher_path,
            &startup_info,
            &process_info)) {
        return fail(GetLastError());
    }
    return wait_for_child(&process_info);
}
