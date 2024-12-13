$(document).ready(function() {
    const paramForm = $('#param-form');
    const arg2Input = $('#arg2');
    const arg2Icon = $('#arg2-icon');
    const currentThemeIcon = $('#currentThemeIcon');
    const submitButton = paramForm.find('button[type="submit"]');
    const buttonsSection = $('#buttons');

    let arg2TooltipTimeout;
    let arg2TooltipVisible = false;
    let currentStatus = '', currentError = '', inErrorSection = false;

    // 函数防抖
    const debounce = (func, delay) => {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => func.apply(this, args), delay);
        };
    };

    // 参数验证
    const isValidArg = (arg) => arg !== null && arg !== "";

    // 切换提交按钮状态
    const toggleSubmitButton = () => {
        const validInputs = arg2Input.val() && !arg2Input.hasClass('is-invalid');
        submitButton.prop('disabled', !validInputs);
    };

    // 显示气泡提示
    const showTooltip = (icon, message, visibleFlag, timeoutVar) => {
        clearTimeout(timeoutVar);
        if (!visibleFlag) {
            icon.removeClass('d-none').attr('data-bs-original-title', message).tooltip('show');
            visibleFlag = true;
        }
        return visibleFlag;
    };

    // 隐藏气泡提示
    const hideTooltip = (icon, timeoutVar, visibleFlag) => {
        clearTimeout(timeoutVar);
        icon.addClass('d-none').tooltip('dispose');
        return false;
    };

    // 验证 arg2
    const validateArg2 = () => {
        const value = arg2Input.val();
        if (value === "") {
            arg2Input.removeClass('is-invalid');
            arg2TooltipVisible = hideTooltip(arg2Icon, arg2TooltipTimeout, arg2TooltipVisible);
            return false;
        } else if (!/^(http:\/\/|https:\/\/)/.test(value)) {
            arg2Input.addClass('is-invalid');
            arg2TooltipTimeout = setTimeout(() => {
                arg2TooltipVisible = showTooltip(arg2Icon, 'Please enter a valid URL starting with http:// or https://.', arg2TooltipVisible, arg2TooltipTimeout);
            }, 1000);
            return false;
        } else {
            arg2Input.removeClass('is-invalid');
            arg2TooltipVisible = hideTooltip(arg2Icon, arg2TooltipTimeout, arg2TooltipVisible);
            return true;
        }
    };

    // 防抖后的验证函数
    const debouncedValidateArg2 = debounce(validateArg2, 300);

    // 输入框事件监听
    arg2Input.on('input', () => {
        debouncedValidateArg2();
        toggleSubmitButton();
    });

    // 初始化页面
    const initializePage = () => {
        const params = new URLSearchParams(window.location.search);
        const arg2 = params.get('u');

        if (isValidArg(arg2)) {
            $('#arg2').val(arg2);
            const validArg2 = validateArg2();
            toggleSubmitButton();
            if (validArg2) {
                startStream(null, arg2);
            }
        } else {
            $('#arg2').val('');
            toggleSubmitButton();
            resetPage();
        }
    };

    // URL 变化监听
    window.addEventListener('popstate', function(event) {
        const params = new URLSearchParams(window.location.search);
        const arg2 = params.get('u');

        if (!isValidArg(arg2)) {
            $('#arg2').val('');
            toggleSubmitButton();
            resetPage();
        } else {
            initializePage();
        }
    });

    // 表单提交处理
    paramForm.on('submit', function(event) {
        event.preventDefault();
        if (!validateArg2()) {
            return;
        }
        const arg2 = $('#arg2').val();
        const url = `/dump?u=${encodeURIComponent(arg2)}`;
        history.pushState(null, '', url);
        startStream(null, arg2);
    });

    // 处理分区按钮点击
    $(document).on('click', '.partition-btn', function() {
        const partitionName = $(this).data('partition');
        const url = $('#arg2').val();
        startStream(partitionName, url);
    });

    // 开始流处理
    const startStream = (partitionName, url) => {
        if (partitionName && !validateArg2()) {
            return;
        }
        $('#output-section').removeClass('hidden');
        submitButton.addClass('hidden');
        $('#status').addClass('hidden').html('');
        $('#error').addClass('hidden').html('');
        $('#file').addClass('hidden');
        $('#file-name').html('');
        $('#loading-bar').removeClass('hidden');
        paramForm.find('input').addClass('disabled').prop('disabled', true);
        buttonsSection.addClass('hidden').html('');

        const eventSource = new EventSource(`/stream?p=${encodeURIComponent(partitionName)}&u=${encodeURIComponent(url)}`);

        eventSource.onmessage = function(event) {
            if (event.data === "SCRIPT_FINISHED") {
                eventSource.close();
                $('#loading-bar').addClass('hidden');
                $('#status').addClass('hidden').html('');
            } else if (event.data.startsWith("STATUS:")) {
                currentStatus += event.data.substring(7).trim();
            } else if (event.data.startsWith("STATUS_END")) {
                $('#status').removeClass('hidden').html(currentStatus.trim().replace(/(<br>\s*){2,}/g, "<br>"));
                currentStatus = '';
            } else if (event.data.startsWith("ERROR:")) {
                inErrorSection = true;
                currentError += event.data.substring(6).trim() + "<br>";
            } else if (event.data.startsWith("ERROR_END")) {
                inErrorSection = false;
                $('#error').html('<span class="error-icon">&#x26A0;</span>' + currentError.trim()).removeClass('hidden');
                $('#loading-bar').addClass('hidden');
                currentError = '';
            } else if (event.data.startsWith("FILE:")) {
                const filePath = event.data.substring(5).trim();
                const fileName = filePath.split('/').pop();
                const subdir = filePath.split('/').slice(-2, -1).join('');
                $('#file-name').html(fileName).off('click').on('click', function() {
                    window.location.href = `/download/zip/${subdir}/${fileName}`;
                });
                $('#file').removeClass('hidden');
                $('#loading-bar').addClass('hidden');
                $('#status').addClass('hidden').html('');
                setTimeout(() => {
                    window.location.href = `/download/zip/${subdir}/${fileName}`;
                }, 100);
            } else if (event.data.startsWith("BUTTONS:")) {
                buttonsSection.html(event.data.substring(8).trim()).removeClass('hidden');
                $('#loading-bar').addClass('hidden');
            } else {
                if (inErrorSection) {
                    currentError += event.data.trim() + "<br>";
                } else {
                    currentStatus += event.data.trim() + "<br>";
                }
            }
        };


        eventSource.onerror = function() {
            $('#error').html('<span class="error-icon">&#x26A0;</span>An error occurred.').removeClass('hidden');
            $('#loading-bar').addClass('hidden');
            eventSource.close();
        };
    };

    // 重置页面
    const resetPage = () => {
        $('#output-section').addClass('hidden');
        submitButton.removeClass('hidden');
        $('#status').addClass('hidden').html('');
        $('#error').addClass('hidden').html('');
        $('#file').addClass('hidden');
        $('#file-name').html('');
        $('#loading-bar').addClass('hidden');
        paramForm.find('input').removeClass('disabled').prop('disabled', false);
        paramForm.removeClass('was-validated');
        buttonsSection.addClass('hidden').html('');
    };

    // 更新主题图标
    const updateThemeIcon = (theme) => {
        const iconClass = theme === 'system' ? 'fa-adjust' : theme === 'light' ? 'fa-sun' : 'fa-moon';
        currentThemeIcon.attr('class', `fas ${iconClass}`);
    };

    // 设置主题
    const setTheme = (theme) => {
        document.body.setAttribute('data-theme', theme === 'system' ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme);
        updateThemeIcon(theme);
    };

    const getCurrentTheme = () => localStorage.getItem('theme') || 'system';

    const saveTheme = (theme) => localStorage.setItem('theme', theme);

    const applyTheme = () => setTheme(getCurrentTheme());

    // 主题切换处理
    $('#themeToggle').on('click', function(event) {
        event.preventDefault();
        const currentTheme = getCurrentTheme();
        const nextTheme = currentTheme === 'light' ? 'dark' : currentTheme === 'dark' ? 'system' : 'light';
        saveTheme(nextTheme);
        setTheme(nextTheme);
    });

    applyTheme();
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
    initializePage();
});