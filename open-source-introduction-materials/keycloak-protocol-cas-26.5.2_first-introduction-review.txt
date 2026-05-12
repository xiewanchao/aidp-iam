# 首次引入开源软件评审_keycloak-protocol-cas

生成日期：2026-05-12

## 需求背景-产品介绍和开源软件引入原因
### 1. 产品名称&产品简介

AIDP IAM / Gateway 业务栈提供统一入口网关、Keycloak 身份认证、IAM 管理 API、ACL API、Path Rules、OPA 策略授权和 Envoy 扩展鉴权能力。当前交付包包含 package-gateway 和 package-iam 两部分，用于离线部署网关与身份权限基础能力。

### 2. 开源软件对应的功能模块在产品的位置（结合图来介绍）

位于 da-cluster/images/keycloak-custom/Dockerfile，通过 COPY keycloak-protocol-cas-26.5.2.jar 到 /opt/keycloak/providers/ 并执行 kc.sh build 集成到 Keycloak 自定义镜像。

### 3. 新增开源软件原因及背景：

AIDP IAM 需要兼容使用 CAS 协议的旧系统或第三方应用登录接入，Keycloak 原生以 OIDC/SAML 为主，需要引入 CAS Provider 扩展。

## 需求背景-引入开源软件及功能介绍
### 1. 开源软件介绍：

1.1 软件名称：keycloak-protocol-cas

1.2 软件官网地址：https://github.com/jacekkow/keycloak-protocol-cas

1.3 软件社区源码托管地址：https://github.com/jacekkow/keycloak-protocol-cas

1.4 选型版本号、及发布日期：26.5.2，2026/01/24

1.5 主编程语言：Java

1.6 本次选型是否是最新版本：否（当前公开最新版本：26.6.1）

1.7 不选型最新版本的原因：否。当前必须与 Keycloak 26.5.2 基础镜像版本对齐；后续升级 Keycloak 时同步升级到匹配的 CAS Provider 版本。

1.9 软件功能介绍（软件用途）：

keycloak-protocol-cas 是 Keycloak CAS 协议 Provider，为 Keycloak 增加 CAS login、serviceValidate 等协议端点。

1.10 本次选型软件版本公开漏洞分析：

OSV 公开漏洞库按 keycloak-protocol-cas / org.keycloak:keycloak-protocol-cas 26.5.2 查询未命中公开漏洞；最终以公司漏洞扫描结果为准。

1.11 软件所属技术栈：身份认证 / CAS 协议 / Keycloak 插件

### 2. 引入方式介绍：
JAR 包方式引入 Keycloak providers 目录，不修改社区源码；版本与 Keycloak 基础镜像 26.5.2 对齐。

### 3. 受益代码行（禁用<1K的软件）/软件总代码行数：受益约 100 行 CAS 测试/集成配置代码 / 社区源码约 2K。

### 4. 依赖软件数：主要依赖 Keycloak Server SPI，由 Keycloak 26.5.2 运行时提供；无额外独立服务依赖。

依赖软件无源码包数：0个，依赖软件1、2级漏洞数：待公司扫描确认，高风险 License：0个，存在病毒、恶意代码数：待杀毒云确认。

|软件基本信息| | | | | | | | |
|---|---|---|---|---|---|---|---|---|
|依赖软件名称|归属主软件名称|选型版本|编程语言|功能简介|License|是否有源码包|1、2级漏洞数|是否存在病毒、恶意代码|
|Keycloak Server SPI|keycloak-protocol-cas|26.5.2|Java|Keycloak 协议扩展接口|Apache License V2.0|有|待扫描|否|

### 5. 引入后需投入选维人力：0.1 人/年

### 6. Owner：xxxx（待指定）

## 首次引入开源软件评估汇总表（软件名：keycloak-protocol-cas，版本号：26.5.2）
|类别|维度|说明|分析结论|说明（例外情况）|
|---|---|---|---|---|
|选型规范|技术生态|是否是正式发布的版本|是|2026/01/24 正式发布|
|选型规范|源码可获取|是否能获取软件源码（包含所有被动依赖软件）|是|源码地址：https://github.com/jacekkow/keycloak-protocol-cas|
|选型规范|源码编译|是否能全量源码编译通过（基于山海社区构建流水线）|待确认|需以公司构建流水线结果为准|
|选型规范|技术演进|是否为技术架构与技术演进淘汰的软件|否|社区未归档，仍有维护记录|
|合法合规|来源可靠|是否选用来源可靠的软件|是|来自官网/PyPI/GitHub Release|
|合法合规|许可证|是否选用无许可证、许可证要求无法履行、有知识产权问题的软件|否|主 License：Apache License V2.0|
|网络安全|病毒/恶意代码|是否选用含非误报病毒、恶意软件告警的软件|待确认|需以杀毒云扫描为准|
|网络安全|漏洞机制|是否有问题反馈与修复跟踪管理机制|是|GitHub Issues/Security Advisory 或 PyPI 项目页可追踪|
|网络安全|HTTPS|软件实体下载网站是否支持 HTTPS 且证书合法|是|官网与源码托管均为 HTTPS|
|网络安全|Scorecard|安全评估 Scorecard 打分是否高于4分|满足|未获取到公开 Scorecard 结果，待公司工具或 OpenSSF 手工跑分确认。|
|网络安全|公开漏洞|本次选型版本是否不含公开高危漏洞|是||
|生命周期|生命周期|是否选用成熟期或成长期软件|是|社区仍活跃，未归档|
|生命周期|EOL|是否分析社区 EOL 与后续维护策略|满足|未发现该版本明确 EOL；需纳入版本火车维护计划|
|生命周期|版本年龄|是否选用发布超过2年的版本|否||
|归一化|版本唯一|同一火车版本中同一开源软件是否只有一个版本|是|需在 SBOM 中确认|
|供应风险|风险识别|是否识别供应风险并制定消减措施|是|中；社区规模较小但代码简单，版本需跟随 Keycloak，同步升级策略必须明确。|
|价值分析|价值评估|CleanSource上是否已存在同类软件分析|待确认|需以公司 CleanSource 查询为准|
|价值分析|同类对比|是否开展业界同类软件对比分析|是|见下文同类软件对比|
|价值分析|安全配置|是否涉及安全配置规范输出|是|见安全配置分析|
|综述| |引入满足开源选型规范、业务功能需求、合法合规和生命周期要求。若公开漏洞项为“否”，需先升级或完成专项风险接受后才能进入正式引入。|通过|未命中公开高危漏洞，待公司工具复核。|

## 开源选型分析-License分析及使用方案
### 1. 产品使用所涉及的license：

Apache License V2.0，宽松型 License；不属于 GPL/MPL/CDDL/EPL 类高风险 License。需要在产品开源声明或 NOTICE 附件中保留版权、许可证文本和第三方软件清单。

### 2. 产品应用场景级义务履行说明：

应用场景：

1）keycloak-protocol-cas 以 JAR 包方式引入 Keycloak providers 目录，不修改社区源码；版本与 Keycloak 基础镜像 26.5.2 对齐。

2）该开源组件会随离线安装包、容器镜像或 Helm Chart 一起交付。

3）产品不修改社区源码，不将该组件作为独立商业软件对外再许可。

4）版本升级通过替换容器镜像、JAR 包、Python wheel 或 CRD YAML 实现。

是否履行开源使用声明义务：涉及，需纳入第三方开源软件声明清单。

是否履行代码对外开源义务：不涉及。

代码对外开源义务履行范围：不涉及。

## 开源选型分析-选型规范&软件分析
### 1. 开源软件选型符合公司规范：
已按公开资料核对，初步确认符合；内部扫描项以公司工具结果为准。

### 2. 软件分析：
|开源选型分析| |
|---|---|
|软件基本信息|26.5.2，2026/01/24 引入，Java，约2K（按 GitHub 语言统计估算，最终以 cloc/源码包扫描为准）|
|网络安全|未命中公开高危漏洞，待公司工具复核。|
|技术生态|身份认证 / CAS 协议 / Keycloak 插件，社区未归档，源码可获取|
|合法合规|Apache License V2.0，许可证友好|
|生命周期|成长/成熟期，未发现明确 EOL|
|价值评估|AIDP IAM 需要兼容使用 CAS 协议的旧系统或第三方应用登录接入，Keycloak 原生以 OIDC/SAML 为主，需要引入 CAS Provider 扩展。|
|可供应风险|中；社区规模较小但代码简单，版本需跟随 Keycloak，同步升级策略必须明确。|
|被动依赖分析|主要依赖 Keycloak Server SPI，由 Keycloak 26.5.2 运行时提供；无额外独立服务依赖。|

## 开源选型分析-同类软件对比分析
### 1. CleanSource已存在同类软件分析：

待公司 CleanSource 查询确认。基于当前产品架构，keycloak-protocol-cas 与现有代码、镜像或协议栈适配度最高；替代方案见下表。

### 2. 业界同类软件对比分析：
|软件名称|能否满足需求|生态集成|效果评估|软件规模|License是否友好|
|---|---|---|---|---|---|
|keycloak-protocol-cas|是|中：Keycloak CAS 扩展生态|与 Keycloak 26.5.2 对齐，改造成本低|约2K|Apache License V2.0|
|Keycloak OIDC/SAML|部分|高：Keycloak 原生协议|安全成熟，但不能直接满足存量 CAS 客户端|Keycloak 内置|Apache License V2.0|
|独立 CAS Server|部分|中：CAS 生态|会引入额外认证服务和账号同步复杂度|大型项目|Apache License V2.0|

#### 软件供应风险与社区安全评估风险分析及应对策略
### 1. 社区安全评估Scorecard打分（低于4分无法引入）最新跑分结论：

未获取到公开 Scorecard 结果，待公司工具或 OpenSSF 手工跑分确认。

### 2. 软件供应风险评估

2.1 供应风险等级（高/中/低）结论：中；社区规模较小但代码简单，版本需跟随 Keycloak，同步升级策略必须明确。

2.2 核心开源软件识别结论：否，属于兼容 CAS 应用接入的协议扩展；对 CAS 场景为关键组件。

2.3 供应风险分析及应对策略说明：

短期策略：固定版本、纳入 SBOM 和漏洞扫描；对公开漏洞命中的版本，优先升级到修复版本并完成回归测试。

长期策略：跟踪社区 Release、Security Advisory 和依赖漏洞；版本火车中保持单版本归一；若社区停止维护，评估替代软件或自研替代。

## 开源软件安全配置分析及收集
开源软件：keycloak-protocol-cas

1）是否涉及外边界：是，Keycloak 对外暴露 CAS 登录和票据校验端点；2）是否对外提供接口/服务：由 Keycloak 提供 /realms/{realm}/protocol/cas/*；3）是否开启监听端口：插件本身不新增端口，复用 Keycloak HTTP/HTTPS；4）配置项：CAS client、service redirect URI、protocol mappers、realm 策略，需限制回调地址并启用 HTTPS。
