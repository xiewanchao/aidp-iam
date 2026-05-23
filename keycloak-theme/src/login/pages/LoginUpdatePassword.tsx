import { useState } from "react";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import Template from "keycloakify/login/Template";

const customMessages = {
    zh: {
        enterPassword: "请输入密码",
        passwordMismatch: "两次密码不一致",
        passwordSetFailed: "密码设置失败，请重试",
        passwordSetSuccess: "密码设置成功",
        useNewPasswordLogin: "请使用新密码重新登录",
        redirectInSeconds: "秒后跳转登录页",
        goToLogin: "去登录"
    },
    en: {
        enterPassword: "Please enter password",
        passwordMismatch: "Passwords do not match",
        passwordSetFailed: "Password setup failed, please try again",
        passwordSetSuccess: "Password set successfully",
        useNewPasswordLogin: "Please login with your new password",
        redirectInSeconds: " seconds until redirect to login page",
        goToLogin: "Go to Login"
    }
};

function getCustomMessage(i18n: I18n, key: keyof typeof customMessages.zh): string {
    const locale = i18n.currentLanguage?.languageTag ?? "zh";
    const messages = customMessages[locale as keyof typeof customMessages] ?? customMessages.zh;
    return messages[key];
}

export default function LoginUpdatePassword(props: { kcContext: KcContext; i18n: I18n }) {
    const { kcContext, i18n } = props;
    const { url, realm } = kcContext as any;
    const { msg } = i18n;

    const [step, setStep] = useState<"update" | "success">("update");
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [countdown, setCountdown] = useState(3);

    async function submitPassword() {
        if (!password || !confirm) {
            alert(getCustomMessage(i18n, "enterPassword"));
            return;
        }

        if (password !== confirm) {
            alert(getCustomMessage(i18n, "passwordMismatch"));
            return;
        }

        const form = document.getElementById("kc-update-password-form") as HTMLFormElement;
        const formData = new FormData(form);
        formData.set("password-new", password);

        try {
            await fetch(form.action, {
                method: "POST",
                body: formData,
                credentials: "include",
                redirect: "manual"
            });
            setStep("success");
            startRedirect();
        } catch (err) {
            console.error("提交失败:", err);
            alert(getCustomMessage(i18n, "passwordSetFailed"));
        }
    }

    function startRedirect() {
        const timer = setInterval(() => {
            setCountdown(prev => {
                if (prev <= 1) {
                    clearInterval(timer);
                    const baseUrl = window.location.origin;
                    window.location.href = `${baseUrl}/realms/${realm.name}/account`;
                    return 0;
                }
                return prev - 1;
            });
        }, 1000);
    }

    return (
        <Template
            kcContext={kcContext}
            i18n={i18n}
            displayMessage={step === "update"}
            doUseDefaultCss={true}
            headerNode={msg("updatePasswordTitle")}
            documentTitle="AIDP"
        >
            <>
            {step === "update" && (
                <div id="step-update" className="step-container">
                    <form id="kc-update-password-form" action={url.loginAction} method="post">
                        <input type="password" id="password-new" name="password-new" autoComplete="new-password" style={{ display: "none" }} />

                        <div className="kcFormGroupClass">
                            <label htmlFor="password-new-display" className="kcLabelClass">{msg("passwordNew")}</label>
                            <input
                                type="password"
                                id="password-new-display"
                                name="password-new-display"
                                className="kcInputClass"
                                autoFocus
                                autoComplete="new-password"
                                value={password}
                                onChange={e => setPassword(e.target.value)}
                            />
                        </div>

                        <div className="kcFormGroupClass">
                            <label htmlFor="password-confirm" className="kcLabelClass">{msg("passwordConfirm")}</label>
                            <input
                                type="password"
                                id="password-confirm"
                                name="password-confirm"
                                className="kcInputClass"
                                autoComplete="new-password"
                                value={confirm}
                                onChange={e => setConfirm(e.target.value)}
                            />
                        </div>

                        <div className="kcFormGroupClass">
                            <div id="kc-form-buttons" className="kcFormButtonsClass">
                                <button
                                    type="button"
                                    className="kcButtonClass kcButtonPrimaryClass kcButtonBlockClass kcButtonLargeClass"
                                    onClick={submitPassword}
                                >
                                    {msg("doSubmit")}
                                </button>
                            </div>
                        </div>
                    </form>
                </div>
            )}

            {step === "success" && (
                <div id="step-success" className="step-container">
                    <div className="success-content">
                        <div className="success-icon">
                            <svg width="50" height="50" viewBox="0 0 24 24" fill="none">
                                <circle cx="12" cy="12" r="10" fill="#e8f5e9" stroke="#28a745" strokeWidth="2" />
                                <path d="M8 12l3 3 5-6" stroke="#28a745" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                        </div>
                        <h3 className="success-title">{getCustomMessage(i18n, "passwordSetSuccess")}</h3>
                        <p className="success-message">{getCustomMessage(i18n, "useNewPasswordLogin")}</p>
                        <p className="redirect-message"><span id="countdown">{countdown}</span>{getCustomMessage(i18n, "redirectInSeconds")}</p>
                        <button
                            type="button"
                            className="login-button"
                            onClick={() => {
                                const baseUrl = window.location.origin;
                                window.location.href = `${baseUrl}/realms/${realm.name}/account`;
                            }}
                        >
                            {getCustomMessage(i18n, "goToLogin")}
                        </button>
                    </div>
                </div>
            )}

            <style>{`
                .step-container { max-width: 400px; margin: 0 auto; }
                .success-content { text-align: center; padding: 30px 20px; }
                .success-icon { margin-bottom: 10px; }
                .success-title { color: #28a745; font-size: 20px; margin-bottom: 10px; }
                .success-message { color: #666; font-size: 14px; margin-bottom: 10px; }
                .redirect-message { color: #888; font-size: 13px; margin-bottom: 15px; }
                .login-button {
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
                .login-button:hover {
                    background: #0056b3;
                }
                #kc-update-password-form input[type="password"] {
                    width: 100%;
                    box-sizing: border-box;
                }
                #kc-message, .kcFeedbackClass, .kcAlertClass, .alert, .alert-info, .pf-c-alert, .pf-c-alert__body {
                    background: transparent !important;
                    border: none !important;
                    padding: 0 !important;
                    margin: 0 0 15px 0 !important;
                    color: #666 !important;
                    font-size: 14px !important;
                    box-shadow: none !important;
                    max-width: 400px !important;
                    width: 100% !important;
                    box-sizing: border-box !important;
                    text-align: left !important;
                }
                #kc-message > div, .kcFeedbackClass > div, .alert > div, .pf-c-alert > div {
                    background: transparent !important;
                    border: none !important;
                    padding: 0 !important;
                    margin: 0 !important;
                    box-shadow: none !important;
                    width: 100% !important;
                }
            `}</style>
            </>
        </Template>
    );
}