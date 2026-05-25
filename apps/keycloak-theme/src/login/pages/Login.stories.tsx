import type { Meta, StoryObj } from "@storybook/react";
import { createKcPageStory } from "../KcPageStory";

const { KcPageStory } = createKcPageStory({ pageId: "login.ftl" });

const meta = {
    title: "login/Login",
    component: KcPageStory
} satisfies Meta<typeof KcPageStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
    render: () => <KcPageStory />
};

export const WithInvalidCredentials: Story = {
    render: () => (
        <KcPageStory
            kcContext={{
                login: { username: "testuser" },
                messagesPerField: {
                    existsError: () => true,
                    get: () => "Invalid username or password."
                } as any
            }}
        />
    )
};

export const WithSocialProviders: Story = {
    render: () => (
        <KcPageStory
            kcContext={{
                social: {
                    displayInfo: true,
                    providers: [
                        { alias: "saml-idp", displayName: "企业身份登录", loginUrl: "#", providerId: "saml" }
                    ]
                }
            }}
        />
    )
};

export const WithRememberMe: Story = {
    render: () => (
        <KcPageStory
            kcContext={{
                realm: { rememberMe: true, registrationAllowed: true, resetPasswordAllowed: true },
                login: { username: "" }
            }}
        />
    )
};
