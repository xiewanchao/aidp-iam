import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import Template from "keycloakify/login/Template";

const customMessages = {
    zh: {
        loginError: "用户名或密码错误。若忘记密码可",
        resetPassword: "重设密码",
        findPassword: "找回密码",
        passwordError: "密码输入错误，请重试",
        otherLoginMethods: "其他登录方式"
    },
    en: {
        loginError: "Invalid username or password. If you forgot your password, you can ",
        resetPassword: "reset password",
        findPassword: "find password",
        passwordError: "Incorrect password, please try again",
        otherLoginMethods: "Other Login Methods"
    }
};

function getCustomMessage(i18n: I18n, key: keyof typeof customMessages.zh): string {
    const locale = i18n.currentLanguage?.languageTag ?? "zh";
    const messages = customMessages[locale as keyof typeof customMessages] ?? customMessages.zh;
    return messages[key];
}

export default function Login(props: { kcContext: KcContext; i18n: I18n }) {
    const { kcContext, i18n } = props;
    const { url, realm, social, login, messagesPerField } = kcContext as any;
    const { msg, msgStr } = i18n;
    const showError = messagesPerField?.existsError?.("username", "password") ?? false;

    return (
        <Template
            kcContext={kcContext}
            i18n={i18n}
            displayMessage={!showError}
            doUseDefaultCss={true}
            headerNode={msg("loginAccountTitle")}
            documentTitle="AIDP"
        >
            <>
                <form id="kc-form-login" action={url.loginAction} method="post">
                    {showError && (
                        <div className="alert-error">
                            {getCustomMessage(i18n, "loginError")}
                            {realm.resetPasswordAllowed && <a href={url.loginResetCredentialsUrl}>{getCustomMessage(i18n, "findPassword")}</a>}
                        </div>
                    )}

                    <div className="kcFormGroupClass">
                        <label htmlFor="username" className="kcLabelClass">
                            {!realm.loginWithEmailAllowed
                                ? msg("username")
                                : !realm.registrationEmailAsUsername
                                    ? msg("usernameOrEmail")
                                    : msg("email")}
                        </label>
                        <input
                            id="username"
                            name="username"
                            className="kcInputClass"
                            type="text"
                            autoFocus
                            autoComplete="username"
                            defaultValue={login?.username ?? ""}
                        />
                    </div>

                    <div className="kcFormGroupClass">
                        <label htmlFor="password" className="kcLabelClass">{msg("password")}</label>
                        <input
                            id="password"
                            name="password"
                            className={`kcInputClass ${showError ? 'input-error' : ''}`}
                            type="password"
                            autoComplete="current-password"
                        />
                        {showError && (
                            <div className="password-error-inline">
                                <svg className="error-icon-x" width="16" height="16" viewBox="0 0 24 24" fill="none">
                                    <circle cx="12" cy="12" r="10" fill="#dc3545"/>
                                    <path d="M15 9l-6 6M9 9l6 6" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
                                </svg>
                                <span>{getCustomMessage(i18n, "passwordError")}</span>
                            </div>
                        )}
                    </div>

                    <div className="kcFormGroupClass kcFormSettingClass">
                        {realm.rememberMe && !realm.registrationEmailAsUsername && (
                            <div className="checkbox">
                                <label>
                                    <input id="rememberMe" name="rememberMe" type="checkbox" defaultChecked={login?.rememberMe} />
                                    {msg("rememberMe")}
                                </label>
                            </div>
                        )}
                        {realm.resetPasswordAllowed && (
                            <span><a href={url.loginResetCredentialsUrl}>{msg("doForgotPassword")}</a></span>
                        )}
                    </div>

                    <div id="kc-form-buttons" className="kcFormButtonsClass">
                        <input
                            name="login"
                            id="kc-login"
                            type="submit"
                            value={msgStr("doLogIn")}
                            className="kcButtonClass kcButtonPrimaryClass kcButtonBlockClass kcButtonLargeClass"
                        />
                    </div>
                </form>

                {social?.providers && (
                    <div id="kc-social-providers" style={{ marginTop: "20px" }}>
                        <div className="social-divider">
                            <span className="social-divider-line"></span>
                            <span className="social-divider-text">{getCustomMessage(i18n, "otherLoginMethods")}</span>
                            <span className="social-divider-line"></span>
                        </div>
                        {social.providers.map((provider: any) => (
                            <a
                                key={provider.alias}
                                id={`social-${provider.alias}`}
                                className="social-btn-outline"
                                href={provider.loginUrl}
                            >
                                {provider.displayName}
                            </a>
                        ))}
                    </div>
                )}

                <style>{`
                    .alert-error {
                        background-color: #f8d7da;
                        border: 1px solid #f5c6cb;
                        color: #721c24;
                        padding: 10px 15px;
                        margin-bottom: 5px;
                        border-radius: 4px;
                        font-size: 14px;
                    }
                    .alert-error a { color: #0056b3; text-decoration: none; }
                    .alert-error a:hover { text-decoration: underline; }
                    .input-error {
                        border-color: #dc3545 !important;
                        box-shadow: 0 0 0 3px rgba(220, 53, 69, 0.2) !important;
                    }
                    .password-error-inline {
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
                    .social-divider {
                        display: flex;
                        align-items: center;
                        margin: 20px 0;
                    }
                    .social-divider-line {
                        flex: 1;
                        height: 1px;
                        background-color: #dee2e6;
                    }
                    .social-divider-text {
                        padding: 0 12px;
                        color: #666;
                        font-size: 14px;
                        font-weight: 500;
                    }
                    .social-btn-outline {
                        display: block;
                        width: 100%;
                        padding: 10px 16px;
                        margin-top: 10px;
                        text-align: center;
                        text-decoration: none;
                        font-size: 14px;
                        font-weight: 500;
                        color: #1a8cff;
                        background-color: transparent;
                        border: 1px solid #1a8cff;
                        border-radius: 4px;
                        cursor: pointer;
                        transition: all 0.2s ease-in-out;
                    }
                    .social-btn-outline:hover {
                        background-color: #1a8cff;
                        color: #fff;
                    }
                    #kc-form-login input[type="text"],
                    #kc-form-login input[type="password"] {
                        width: 100%;
                        box-sizing: border-box;
                    }
                `}</style>
            </>
        </Template>
    );
}
