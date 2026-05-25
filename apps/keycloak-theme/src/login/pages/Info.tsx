import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import Template from "keycloakify/login/Template";

export default function Info(props: { kcContext: KcContext; i18n: I18n }) {
    const { kcContext, i18n } = props;
    const { realm } = kcContext as any;

    return (
        <Template
            kcContext={kcContext}
            i18n={i18n}
            displayMessage={false}
            doUseDefaultCss={true}
            headerNode={null}
            documentTitle="AIDP"
        >
            <>
            <div id="success-modal" className="modal-overlay">
                <div className="modal-container">
                    <div className="modal-header">
                        <div className="success-icon">
                            <svg width="60" height="60" viewBox="0 0 24 24" fill="none">
                                <circle cx="12" cy="12" r="10" fill="#e8f5e9" stroke="#28a745" strokeWidth="2" />
                                <path d="M8 12l3 3 5-6" stroke="#28a745" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                        </div>
                        <h3>请往邮箱重设密码</h3>
                    </div>
                    <div className="modal-body">
                        <p className="success-message">重设密码链接已发送至您的邮箱，请往邮箱重设密码</p>
                        <div className="tips-box">
                            <div className="tip">⏰ 邮件有效期：30分钟</div>
                            <div className="tip">🔐 链接仅可使用一次</div>
                            <div className="tip">🔍 如未收到，请检查垃圾邮件文件夹</div>
                        </div>
                    </div>
                    <div className="modal-footer">
                        <a
                            href={`${window.location.origin}/realms/${realm.name}/account`}
                            className="kcButtonClass kcButtonPrimaryClass"
                            style={{ textDecoration: "none" }}
                        >
                            我知道了
                        </a>
                    </div>
                </div>
            </div>

            <style>{`
                .modal-overlay {
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background-color: rgba(0, 0, 0, 0.5);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    z-index: 1000;
                }
                .modal-container {
                    background-color: white;
                    padding: 30px;
                    border-radius: 8px;
                    max-width: 450px;
                    width: 90%;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }
                .modal-header { text-align: center; }
                .modal-header h3 {
                    margin: 15px 0 20px 0;
                    font-size: 22px;
                    color: #28a745;
                }
                .success-icon { margin-bottom: 5px; }
                .modal-body { text-align: center; }
                .success-message {
                    color: #495057;
                    font-size: 15px;
                    margin-bottom: 15px;
                    line-height: 1.6;
                }
                .tips-box {
                    background-color: #f8f9fa;
                    padding: 15px 20px;
                    border-radius: 8px;
                    text-align: left;
                }
                .tip { color: #495057; font-size: 14px; margin: 8px 0; }
                .modal-footer { margin-top: 25px; text-align: center; }
                .modal-footer a {
                    text-decoration: none;
                    min-width: 120px;
                    display: inline-block;
                }
            `}</style>
            </>
        </Template>
    );
}