import { useState } from "react";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import Template from "keycloakify/login/Template";

type Step = "input" | "loading" | "confirm" | "no-email" | "success";

const customMessages = {
    zh: {
        nextStep: "下一步",
        queryingUser: "正在查询用户信息...",
        verifyEmailTitle: "验证邮箱地址",
        systemEmailPrompt: "系统显示您的邮箱地址为：",
        enterFullEmail: "请输入完整邮箱地址以确认：",
        enterEmailPlaceholder: "请输入完整邮箱地址",
        emailValidityWarning: "邮件有效期30分钟，链接仅可使用一次",
        confirmSend: "确认发送",
        goBack: "返回",
        cannotSendEmail: "无法发送邮件",
        contactAdminResetPwd: "联系管理员重设密码",
        noEmailBindHint: "验证到该账户未绑定邮箱，无法自行重设密码，请联系管理员协助",
        noEmailConfigured: "您的账号未配置邮箱，请联系管理员重设密码",
        checkEmailResetPassword: "前往邮箱重设密码",
        resetLinkSent: "找回密码链接已发送至您的邮箱，若 1 分钟未收到，建议查看邮箱垃圾箱",
        emailValidity30Min: "邮件有效期30分钟",
        linkUseOnce: "链接仅可使用一次",
        checkSpamFolder: "未收到请检查垃圾邮件",
        returnToLogin: "返回登录",
        enterUsernameOrEmail: "请输入用户名或邮箱地址",
        systemErrorQuery: "系统错误，无法查询用户信息",
        enterEmailAddress: "请输入邮箱地址",
        emailMismatch: "邮箱地址不匹配",
        iKnow: "我知道了",
        prevStep: "上一步",
        userNotFound: "用户不存在"
    },
    en: {
        nextStep: "Next",
        queryingUser: "Querying user information...",
        verifyEmailTitle: "Verify Email Address",
        systemEmailPrompt: "Your email address is:",
        enterFullEmail: "Please enter the full email address to confirm:",
        enterEmailPlaceholder: "Enter full email address",
        emailValidityWarning: "Email valid for 30 minutes, link can only be used once",
        confirmSend: "Confirm and Send",
        goBack: "Back",
        cannotSendEmail: "Cannot Send Email",
        contactAdminResetPwd: "Contact Admin to Reset Password",
        noEmailBindHint: "This account has no email bound. You cannot reset password yourself. Please contact your administrator for assistance.",
        noEmailConfigured: "Your account has no email configured. Please contact administrator to reset password.",
        checkEmailResetPassword: "Go to Email to Reset Password",
        resetLinkSent: "Password reset link has been sent to your email. If not received within 1 minute, please check your spam folder",
        emailValidity30Min: "Email valid for 30 minutes",
        linkUseOnce: "Link can only be used once",
        checkSpamFolder: "If not received, please check spam folder",
        returnToLogin: "Return to Login",
        enterUsernameOrEmail: "Please enter username or email address",
        systemErrorQuery: "System error, cannot query user information",
        enterEmailAddress: "Please enter email address",
        emailMismatch: "Email address does not match",
        iKnow: "I Know",
        prevStep: "Previous",
        userNotFound: "User not found"
    }
};

function getCustomMessage(i18n: I18n, key: keyof typeof customMessages.zh): string {
    const locale = i18n.currentLanguage?.languageTag ?? "zh";
    const messages = customMessages[locale as keyof typeof customMessages] ?? customMessages.zh;
    return messages[key];
}

export default function LoginResetPassword(props: { kcContext: KcContext; i18n: I18n }) {
    const { kcContext, i18n } = props;
    const { url, realm, auth } = kcContext as any;
    const { msg } = i18n;

    const [step, setStep] = useState<Step>("input");
    const [username, setUsername] = useState(auth?.attemptedUsername ?? "");
    const [email, setEmail] = useState("");
    const [maskedEmail, setMaskedEmail] = useState("");
    const [emailError, setEmailError] = useState("");
    const [usernameError, setUsernameError] = useState("");

    async function checkUserEmail() {
        if (!username.trim()) {
            setUsernameError(getCustomMessage(i18n, "enterUsernameOrEmail"));
            return;
        }

        setUsernameError("");
        setStep("loading");

        try {
            const apiUrl = `${window.location.origin}/realms/${realm.name}/email-mask/get-email-masked?username=${encodeURIComponent(username.trim())}`;
            const res = await fetch(apiUrl);
            
            if (!res.ok) {
                setStep("input");
                if (res.status === 404) {
                    setUsernameError(getCustomMessage(i18n, "systemErrorQuery") + " (API not found)");
                } else {
                    setUsernameError(getCustomMessage(i18n, "systemErrorQuery") + ` (${res.status})`);
                }
                return;
            }

            const data = await res.json();

            if (!data.found) {
                // 用户不存在，显示页面提示
                setStep("input");
                setUsernameError(getCustomMessage(i18n, "userNotFound"));
                return;
            }

            if (!data.hasEmail) {
                setStep("no-email");
            } else {
                setMaskedEmail(data.maskedEmail);
                setEmail("");
                setEmailError("");
                setStep("confirm");
            }
        } catch (err: any) {
            console.error("查询失败:", err);
            const errorMsg = getCustomMessage(i18n, "systemErrorQuery") + ` (错误: ${err?.message || err})`;
            setStep("input");
            setUsernameError(errorMsg);
        }
    }

    async function verifyAndSubmit() {
        if (!email.trim()) {
            setEmailError(getCustomMessage(i18n, "enterEmailAddress"));
            return;
        }

        setStep("loading");

        try {
            const apiUrl = `${window.location.origin}/realms/${realm.name}/email-mask/verify-email?username=${encodeURIComponent(username.trim())}&email=${encodeURIComponent(email.trim())}`;
            const res = await fetch(apiUrl);
            
            if (!res.ok) {
                setEmailError(getCustomMessage(i18n, "systemErrorQuery") + ` (${res.status})`);
                setStep("confirm");
                return;
            }

            const data = await res.json();

            if (!data.match) {
                setEmailError(getCustomMessage(i18n, "emailMismatch"));
                setStep("confirm");
                return;
            }

            setEmailError("");
            
            // 提交表单发送邮件
            setStep("loading");
            const form = document.getElementById("kc-reset-password-form") as HTMLFormElement;
            const formData = new FormData(form);
            
            try {
                await fetch(form.action, {
                    method: "POST",
                    body: formData,
                    credentials: "include",
                    redirect: "manual"
                });
                // 显示成功页面
                setStep("success");
            } catch (submitErr) {
                console.error("提交失败:", submitErr);
                alert(getCustomMessage(i18n, "systemErrorQuery"));
                setStep("confirm");
            }
        } catch (err: any) {
            console.error("验证失败:", err);
            const errorMsg = getCustomMessage(i18n, "systemErrorQuery") + ` (错误: ${err?.message || err})`;
            setEmailError(errorMsg);
            alert(errorMsg);
            setStep("confirm");
        }
    }

    return (
        <Template
            kcContext={kcContext}
            i18n={i18n}
            displayMessage={step === "input"}
            doUseDefaultCss={true}
            headerNode={msg("emailForgotTitle")}
            documentTitle="AIDP"
        >
            <>
            {/* Hidden form that always exists for submission */}
            <form id="kc-reset-password-form" action={url.loginAction} method="post" style={{ display: "none" }}>
                <input type="text" name="username" value={username} readOnly />
            </form>

            {step === "input" && (
                <div id="step-input" className="step-container">
                    <div className="kcFormGroupClass">
                        <label htmlFor="username-display" className="kcLabelClass">
                            {!realm.loginWithEmailAllowed
                                ? msg("username")
                                : !realm.registrationEmailAsUsername
                                    ? msg("usernameOrEmail")
                                    : msg("email")}
                        </label>
                        <input
                            type="text"
                            id="username-display"
                            className={`kcInputClass ${usernameError ? 'input-error' : ''}`}
                            autoFocus
                            value={username}
                            onChange={e => {
                                setUsername(e.target.value);
                                if (usernameError) setUsernameError("");
                            }}
                        />
                        {usernameError && (
                            <div className="username-error-inline">
                                <svg className="error-icon-x" width="16" height="16" viewBox="0 0 24 24" fill="none">
                                    <circle cx="12" cy="12" r="10" fill="#dc3545"/>
                                    <path d="M15 9l-6 6M9 9l6 6" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
                                </svg>
                                <span>{usernameError}</span>
                            </div>
                        )}
                    </div>
                    <div className="kcFormGroupClass kcFormSettingClass">
                        <span><a href={url.loginUrl}>{msg("backToLogin")}</a></span>
                    </div>
                    <div id="kc-form-buttons" className="kcFormButtonsClass">
                        <button
                            type="button"
                            id="kc-check-btn"
                            className="kcButtonClass kcButtonPrimaryClass kcButtonBlockClass kcButtonLargeClass"
                            onClick={checkUserEmail}
                        >
                            {getCustomMessage(i18n, "nextStep")}
                        </button>
                    </div>
                </div>
            )}

            {step === "loading" && (
                <div id="step-loading" className="step-container">
                    <div className="loading-content">
                        <div className="loading-spinner"></div>
                        <p className="loading-text">{getCustomMessage(i18n, "queryingUser")}</p>
                    </div>
                </div>
            )}

            {step === "confirm" && (
                <div id="step-confirm" className="step-container">
                    <div className="confirm-content">
                        <h3 className="confirm-title">{getCustomMessage(i18n, "verifyEmailTitle")}</h3>
                        <p className="confirm-instruction">{getCustomMessage(i18n, "systemEmailPrompt")}</p>
                        <div className="email-display-box">
                            <span className="masked-email">{maskedEmail}</span>
                        </div>
                        <p className="confirm-instruction">{getCustomMessage(i18n, "enterFullEmail")}</p>
                        <div className="email-input-wrapper">
                            <input
                                type="email"
                                id="email-input"
                                className="email-input-field"
                                placeholder={getCustomMessage(i18n, "enterEmailPlaceholder")}
                                value={email}
                                onChange={e => setEmail(e.target.value)}
                            />
                            {emailError && <span className="email-error-message">{emailError}</span>}
                        </div>
                        <p className="warning-text">⏰ {getCustomMessage(i18n, "emailValidityWarning")}</p>
                        <div className="action-buttons">
                            <button
                                type="button"
                                className="kcButtonClass kcButtonPrimaryClass"
                                onClick={verifyAndSubmit}
                            >
                                {getCustomMessage(i18n, "confirmSend")}
                            </button>
                            <button
                                type="button"
                                className="kcButtonClass kcButtonDefaultClass"
                                onClick={() => setStep("input")}
                            >
                                {getCustomMessage(i18n, "goBack")}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {step === "no-email" && (
                <div id="step-no-email" className="step-container">
                    <div className="no-email-content">
                        <div className="no-email-icon">
                            <svg width="60" height="60" viewBox="0 0 24 24" fill="none">
                                <circle cx="12" cy="12" r="10" fill="#fff3e0" stroke="#f44336" strokeWidth="2" />
                                <path d="M12 8v4M12 16h.01" stroke="#f44336" strokeWidth="2" strokeLinecap="round" />
                            </svg>
                        </div>
                        <h2 className="no-email-title">{getCustomMessage(i18n, "contactAdminResetPwd")}</h2>
                        <p className="no-email-hint">{getCustomMessage(i18n, "noEmailBindHint")}</p>
                        <div className="no-email-actions">
                            <button
                                type="button"
                                className="kcButtonClass kcButtonPrimaryClass kcButtonBlockClass kcButtonLargeClass"
                                onClick={() => window.location.href = url.loginUrl}
                            >
                                {getCustomMessage(i18n, "iKnow")}
                            </button>
                            <button
                                type="button"
                                className="kcButtonClass kcButtonDefaultClass kcButtonBlockClass kcButtonLargeClass"
                                onClick={() => setStep("input")}
                            >
                                {getCustomMessage(i18n, "prevStep")}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {step === "success" && (
                <div id="step-success" className="step-container">
                    <div className="success-content">
                        <div className="success-icon">
                            <svg width="60" height="60" viewBox="0 0 24 24" fill="none">
                                <circle cx="12" cy="12" r="10" fill="#e8f5e9" stroke="#28a745" strokeWidth="2" />
                                <path d="M8 12l3 3 5-6" stroke="#28a745" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                        </div>
                        <h2 className="success-main-title">{getCustomMessage(i18n, "checkEmailResetPassword")}</h2>
                        <p className="success-description">{getCustomMessage(i18n, "resetLinkSent")}</p>
                        <button
                            type="button"
                            className="i-know-button"
                            onClick={() => {
                                const baseUrl = window.location.origin;
                                window.location.href = `${baseUrl}/realms/${realm.name}/account`;
                            }}
                        >
                            {getCustomMessage(i18n, "iKnow")}
                        </button>
                    </div>
                </div>
            )}

            <style>{`
                .step-container { max-width: 400px; margin: 0 auto; }
                #kc-reset-password-form input[type="text"] {
                    width: 100%;
                    box-sizing: border-box;
                }
                #step-input input[type="text"] {
                    width: 100%;
                    box-sizing: border-box;
                    padding: 12px 16px;
                }
                .input-error {
                    border-color: #dc3545 !important;
                    box-shadow: 0 0 0 3px rgba(220, 53, 69, 0.2) !important;
                }
                .username-error-inline {
                    display: flex;
                    align-items: center;
                    gap: 6px;
                    color: #dc3545;
                    font-size: 13px;
                    margin-top: 8px;
                }
                .error-icon-x {
                    flex-shrink: 0;
                }
                .loading-content { text-align: center; padding: 40px 20px; }
                .loading-spinner {
                    width: 40px; height: 40px;
                    border: 3px solid #e9ecef;
                    border-top: 3px solid #1a8cff;
                    border-radius: 50%;
                    margin: 0 auto 15px auto;
                    animation: spin 0.8s linear infinite;
                }
                @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                .loading-text { color: #333; font-size: 14px; }
                .confirm-content, .error-content, .success-content { text-align: center; padding: 30px 20px; }
                .confirm-title { color: #333; font-size: 20px; font-weight: 600; margin-bottom: 20px; }
                .confirm-instruction { color: #666; font-size: 14px; margin-bottom: 8px; }
                .email-display-box {
                    background-color: #f8f9fa;
                    padding: 12px 16px;
                    border-radius: 6px;
                    margin-bottom: 20px;
                    border: 1px solid #dee2e6;
                }
                .masked-email { font-weight: 500; color: #1a8cff; font-size: 16px; }
                .email-input-wrapper { margin-bottom: 15px; }
                .email-input-field {
                    width: 100%;
                    box-sizing: border-box;
                    padding: 12px 16px;
                    border-radius: 6px;
                    border: 1px solid #dee2e6;
                    font-size: 16px;
                    text-align: center;
                    background-color: #fff;
                }
                .email-input-field:focus { outline: none; border-color: #1a8cff; }
                .email-error-message { color: #dc3545; font-size: 13px; margin-top: 6px; display: block; }
                .warning-text {
                    color: #666;
                    background-color: #f8f9fa;
                    padding: 10px 12px;
                    border-radius: 6px;
                    font-size: 13px;
                    border-left: 3px solid #ffc107;
                    margin-bottom: 20px;
                }
                .error-icon, .success-icon { margin-bottom: 15px; }
                .error-title { color: #dc3545; font-size: 20px; font-weight: 600; margin-bottom: 10px; }
                .error-message { color: #666; font-size: 14px; margin-bottom: 20px; }
                .success-main-title { color: #28a745; font-size: 24px; font-weight: 600; margin-bottom: 15px; }
                .success-description { color: #666; font-size: 14px; margin-bottom: 25px; line-height: 1.6; }
                .tips-box {
                    background-color: #f8f9fa;
                    padding: 12px 16px;
                    border-radius: 6px;
                    text-align: left;
                    margin-bottom: 20px;
                }
                .tip { color: #666; font-size: 13px; margin: 6px 0; }
                .action-buttons { margin-top: 20px; display: flex; gap: 10px; justify-content: center; }
                .action-buttons button, .action-buttons a { min-width: 100px; padding: 8px 16px; }
                .i-know-button {
                    background: #007bff;
                    color: #fff;
                    border: none;
                    padding: 12px 16px;
                    font-size: 14px;
                    font-weight: 600;
                    border-radius: 4px;
                    cursor: pointer;
                    transition: background 0.2s;
                    width: 100%;
                    box-sizing: border-box;
                    display: block;
                }
                .i-know-button:hover {
                    background: #0056b3;
                }
                .no-email-content { text-align: center; padding: 30px 20px; }
                .no-email-icon { margin-bottom: 15px; display: flex; justify-content: center; }
                .no-email-title { color: #dc3545; font-size: 20px; font-weight: 600; margin-bottom: 10px; }
                .no-email-hint { color: #666; font-size: 14px; margin-top: 6px; }
                .no-email-actions { display: flex; flex-direction: column; gap: 10px; margin-top: 25px; }
            `}</style>
            </>
        </Template>
    );
}
