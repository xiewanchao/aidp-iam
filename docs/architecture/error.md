## 这个frontend-api-reference.md中一个很大问题
- 这些接口的Method没有按照 on-onboarding中的API规范来，比如创建是 PUT， 更新是 PATCH，这是个很大的问题


## 用户管理

- 用户列表：GET /AccessManager/Tenants/{tenant_id}/Users
    - 缺少创建时间参数
    - 缺少返回 totalCount，前端无法感知条数，以及分页机制有问题

- 用户详情：GET /AccessManager/Tenants/{tenant_id}/Users/{user_id}/Details
    - 用户详情接口报错

- 批量创建用户：PUT /AccessManager/Tenants/{tenant_id}/Users/BatchCreate
    - 超过多少条会报错 payload too large？需要前端限制最大条数吗？

- 创建用户：PUT /AccessManager/Tenants/{tenant_id}/Users
    - 创建用户新增了邮箱，批量导入和创建接口要查看下是否有实现



## 用户组

- 用户组列表：GET /AccessManager/Tenants/{tenant_id}/Groups
    - 缺少返回totalCount，以及分页同样有问题
    - 缺少创建用户组的时间

- 创建用户组：PUT /AccessManager/Tenants/{tenant_id}/Groups
    - 没法传描述信息，接口传 attributes 会报错，input should be a valid dictionary

- 用户组详情：这是哪个接口啊？frontend-api-reference中用户组管理部分格式错乱
    - 同样没哟传用户加入用户组的时间

- IdP / SAML 管理
    - 这部分的接口都会报错：404 的问题