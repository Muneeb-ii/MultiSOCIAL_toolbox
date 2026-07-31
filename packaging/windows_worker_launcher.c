/*
 * Native trampoline for the private Windows analysis worker.
 *
 * The packaged GUI never shares native runtime files with this CPython worker.
 * For GUI-originated launches the worker speaks on an authenticated loopback
 * socket, so this launcher can create it without inheriting GUI standard
 * handles. Direct command-line diagnostics retain their stdio protocol.
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

int wmain(void) {
    WCHAR launcher_path[MAX_PATH];
    WCHAR worker_python[MAX_PATH];
    WCHAR worker_script[MAX_PATH];
    WCHAR child_directory[MAX_PATH];
    WCHAR command_line[MAX_PATH * 2 + 64];
    WCHAR *separator;
    STARTUPINFOW startup_info;
    PROCESS_INFORMATION process_info;
    BOOL socket_protocol;

    if (!GetModuleFileNameW(NULL, launcher_path, ARRAYSIZE(launcher_path))) {
        return fail(GetLastError());
    }
    separator = wcsrchr(launcher_path, L'\\');
    if (separator == NULL) {
        return fail(ERROR_BAD_PATHNAME);
    }
    *separator = L'\0';
    if (StringCchPrintfW(worker_python, ARRAYSIZE(worker_python), L"%s\\%s", launcher_path, WORKER_PYTHON) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }
    if (StringCchPrintfW(worker_script, ARRAYSIZE(worker_script), L"%s\\%s", launcher_path, WORKER_SCRIPT) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }
    if (GetFileAttributesW(worker_python) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(worker_script) == INVALID_FILE_ATTRIBUTES) {
        return fail(GetLastError());
    }

    socket_protocol = GetEnvironmentVariableW(
        L"MULTISOCIAL_WORKER_PROTOCOL_HOST", NULL, 0
    ) > 0;
    /* Reset both legacy and modern process DLL-directory mechanisms. */
    SetDllDirectoryW(NULL);
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    reset_pyinstaller_environment();
    /* OpenSMILE's Windows bridge incorrectly encodes its working directory as
       ASCII. The installed application path may legitimately contain Unicode.
       Socket-mode workers use absolute interpreter, module, asset, and output
       paths, so a stable system directory removes that native-library limit
       without changing any user-visible input or output path. */
    if (socket_protocol) {
        DWORD directory_length = GetWindowsDirectoryW(
            child_directory, ARRAYSIZE(child_directory)
        );
        if (directory_length == 0 || directory_length >= ARRAYSIZE(child_directory)) {
            return fail(GetLastError());
        }
    } else if (StringCchCopyW(child_directory, ARRAYSIZE(child_directory), launcher_path) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }

    ZeroMemory(&startup_info, sizeof(startup_info));
    startup_info.cb = sizeof(startup_info);
    if (!socket_protocol) {
        startup_info.dwFlags = STARTF_USESTDHANDLES;
        startup_info.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        startup_info.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
        startup_info.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    }
    ZeroMemory(&process_info, sizeof(process_info));
    if (StringCchPrintfW(command_line, ARRAYSIZE(command_line), L"\"%s\" -I \"%s\"", worker_python, worker_script) != S_OK) {
        return fail(ERROR_BUFFER_OVERFLOW);
    }
    if (!CreateProcessW(
            worker_python,
            command_line,
            NULL,
            NULL,
            socket_protocol ? FALSE : TRUE,
            CREATE_NO_WINDOW,
            NULL,
            child_directory,
            &startup_info,
            &process_info)) {
        return fail(GetLastError());
    }
    return wait_for_child(&process_info);
}
