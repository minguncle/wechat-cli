on run argv
    set targetName to item 1 of argv
    set messageText to item 2 of argv
    set sendMode to item 3 of argv
    tell application "WeChat" to activate
    tell application "System Events"
        if not (exists process "WeChat") then error "微信未运行"
        tell process "WeChat"
            set frontmost to true
            keystroke "f" using command down
            delay 0.4
            set the clipboard to targetName
            keystroke "v" using command down
            delay 1
            -- Select only an accessible result whose label exactly matches the target.
            set targetElement to missing value
            set matchCount to 0
            repeat with elementRef in entire contents of front window
                try
                    if value of elementRef is targetName and role of elementRef is "AXStaticText" then
                        set matchCount to matchCount + 1
                        set targetElement to elementRef
                    end if
                end try
            end repeat
            if matchCount > 1 then error "存在多个同名搜索结果"
            if targetElement is missing value then error "无法确认搜索结果，请检查辅助功能授权及微信界面版本"
            click targetElement
            delay 0.7
            -- A focused message editor is required; never paste into an arbitrary window.
            set activeEditor to value of attribute "AXFocusedUIElement"
            if role of activeEditor is not "AXTextArea" then error "未能确认聊天输入区域"
            if value of activeEditor is not "" then error "聊天输入框存在草稿，请先处理草稿"
            if messageText is not "" then
                set the clipboard to messageText
                keystroke "v" using command down
                key code 36
                delay 0.4
            end if
            if (count argv) > 3 then
                repeat with i from 4 to count argv
                    set fileAlias to POSIX file (item i of argv) as alias
                    if sendMode is "images" then
                        set the clipboard to (read fileAlias as «class PNGf»)
                    else
                        set the clipboard to fileAlias
                    end if
                    keystroke "v" using command down
                    delay 1
                    key code 36
                    delay 0.4
                end repeat
            end if
        end tell
    end tell
    return "桌面发送操作已执行，尚未确认对端收取"
end run
