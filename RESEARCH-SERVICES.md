# Replicable SaaS candidates, ranked for Venu

Research snapshot: 2026-10-30. Ranked by estimated recurring customer value per solo-build hour, with extra weight for replacing a paid tier. This is a shortlist of the best-fit candidates from the prior research, not a claim that all 50 earlier products are equally buildable.

**Auth note:** “Auth model” below is the recommended clean-room auth for Venu’s replica, not a claim about undocumented vendor internals. Exact feature names and prices are linked to official product/docs pages. “Third-party key” means a customer must connect a non-replica service for the product to deliver its main value; OAuth installation counts as a required connection even if customers do not paste a key.

## 1. Back in Stock by Amp
- **What it costs:** Free plan; standard paid tiers **$19, $49, $79, $99/month**. Shopify Plus tiers are **$49, $99, $199, $299/month**. SMS is billed per message. [Official pricing](https://backinstock.org/pricing)
- **Core API surfaces/features:** product/variant notification registration; restock event and queued email/SMS notification; customizable storefront form/button; preorder capture; activity reports, integrations, and Pro-tier API/webhooks.
- **Auth model:** merchant installs and authorizes a Shopify app with least-privilege scopes; customers opt in through a storefront form without needing an account. [Shopify app auth](https://shopify.dev/docs/apps/build/authentication-authorization)
- **Third-party key needed:** **Yes** for live Shopify inventory; SMS delivery needs an SMS provider. Use OAuth/app installation rather than asking merchants to paste a Shopify key.
- **Build difficulty:** **S** for a Shopify-only email MVP; **M** once SMS, preorders, translations, and integrations are included.

## 2. Churn Buster
- **What it costs:** Dunning or Cancel Flows starts at **$269/month**; both start at **$430/month**, varying with MRR. [Official pricing](https://churnbuster.io/pricing/)
- **Core API surfaces/features:** failed-payment dunning campaigns; cancellation flows; recovery/cancellation analytics; customer segmentation and A/B tests.
- **Auth model:** tenant login plus merchant-authorized billing-provider connection (Stripe or equivalent), scoped credentials, signed webhook verification, and a separate mail-provider connection. The Stripe integration method for a Venu-built version is a design choice, not verified as Churn Buster’s private auth flow. [Stripe API auth](https://docs.stripe.com/api/authentication)
- **Third-party key needed:** **Yes** for live billing data and payment recovery; a mail provider is also needed to send messages.
- **Build difficulty:** **M** for one Stripe-first MVP; **L** for production-grade multi-provider coverage and recovery edge cases.

## 3. Bannerbear
- **What it costs:** **$49/month** Automate, **$149/month** Scale, **$299/month** Enterprise; plans meter API/AI credits. [Official pricing](https://www.bannerbear.com/pricing/)
- **Core API surfaces/features:** template-based image rendering; animation/video rendering; asynchronous render status/result retrieval; workflow/forms and integrations; project/workspace management with usage/credit tracking. The vendor documents a REST API and API-key-scoped projects. [API reference](https://developers.bannerbear.com/v2/)
- **Auth model:** tenant workspace plus project-scoped bearer API key; admin/master key only for account-level project administration. [Official authentication docs](https://developers.bannerbear.com/v2/)
- **Third-party key needed:** **No** for core template rendering. Optional AI, storage, or external workflow integrations may require their own credentials.
- **Build difficulty:** **M** for image templates and a render queue; **L** for video, AI tools, broad integrations, and credit accounting.

## 4. Zigpoll
- **What it costs:** Free Lite plan; paid plans are advertised from **$29/month**, but exact current tier amounts were not fully exposed in the official-page capture. Mark detailed tier pricing **UNVERIFIED**. [Official pricing](https://www.zigpoll.com/pricing)
- **Core API surfaces/features:** survey/question create-update-list; response and participant collection/export; translations; signed webhooks; storefront/checkout survey delivery and conditional actions. API access is Premium or higher. [Official API docs](https://docs.zigpoll.com/web-api)
- **Auth model:** tenant/workspace login plus role-scoped API key in the `Authorization` header; signed webhooks; public storefront embed identifier separated from admin credentials. [Official API auth/roles](https://docs.zigpoll.com/web-api)
- **Third-party key needed:** **No** for standalone surveys and response storage; **Yes** for Shopify order context or third-party email/SMS follow-up integrations.
- **Build difficulty:** **M** for survey builder, embed, response store, and exports; **L** for the full Shopify/attribution/integration surface.

## 5. Geckoboard
- **What it costs:** Essentials from **$79/month** and Performance from **$319/month**, billed annually; Enterprise is custom. [Official pricing](https://www.geckoboard.com/pricing/)
- **Core API surfaces/features:** dashboard/widget composition; custom dataset definition and data append/replace; live connector refresh; TV-screen dashboard rotation; alerts, snapshots, and drilldown. [Datasets API](https://developer.geckoboard.com/)
- **Auth model:** tenant accounts/roles plus a scoped dataset API key; external data connectors use OAuth or provider credentials. The official Datasets API documents API-key authentication. [Official API docs](https://developer.geckoboard.com/)
- **Third-party key needed:** **No** for dashboards fed by the replica’s own dataset-push API; **Yes** for turnkey connectors to Shopify, Zendesk, Aircall, or other providers.
- **Build difficulty:** **M** for custom datasets, widgets, and TV mode; **L** for a broad connector catalog and enterprise controls.

## 6. Smile.io
- **What it costs:** Shopify listing shows plans at **$15/month** and **$199/month**; official pricing page lists **$15, $79, and $199** plan levels, with Plus/Enterprise pricing not consistently visible in the page capture. Detailed upper-tier amounts are **UNVERIFIED** here. [Official pricing](https://smile.io/pricing) · [Shopify listing](https://apps.shopify.com/smile-io)
- **Core API surfaces/features:** points earning ledger and reward redemption; referral links/attribution; VIP tiers and expiry rules; embedded loyalty page/customer hub; loyalty analytics and Shopify Flow/metafield integration. API access is listed for higher plans.
- **Auth model:** tenant account plus merchant-authorized Shopify/BigCommerce app installation and scoped platform token; shopper identity is linked to the store’s customer identity. [Shopify app auth](https://shopify.dev/docs/apps/build/authentication-authorization)
- **Third-party key needed:** **Yes** for ecommerce order/customer data; merchant installs/authorizes the platform app.
- **Build difficulty:** **L** for credible ledger correctness, fraud controls, checkout/POS behavior, and platform integrations.

## 7. Baremetrics
- **What it costs:** **UNVERIFIED** from the official pricing material captured in this pass; do not use third-party price claims as a build assumption. [Official product/pricing page](https://baremetrics.com/pricing)
- **Core API surfaces/features:** subscription analytics and SaaS metrics; failed-payment recovery; cancellation insights; financial forecasting; customer/subscription ingestion for unsupported billing systems. [Official product overview](https://baremetrics.com/) · [Developer API](https://developers.baremetrics.com/reference/introduction)
- **Auth model:** tenant account plus merchant-authorized billing-provider access; API keys for server-to-server ingestion/API access. Baremetrics’ docs say API keys are available in account settings. [Official developer docs](https://developers.baremetrics.com/reference/introduction)
- **Third-party key needed:** **Yes** for live metrics from Stripe/Recurly/Braintree or another billing source; custom ingestion can use the replica’s own API.
- **Build difficulty:** **M** for Stripe-only MRR dashboards; **L** for accurate multi-provider calculations, recovery, forecasting, and historical reconciliation.

## Backlog comparison
- **Beats `mail-engine`:** Churn Buster is a more monetizable, narrow recovery workflow, but it complements rather than replaces generic email infrastructure.
- **Beats `form-engine`:** Zigpoll is the stronger first vertical slice: post-purchase surveys, response analytics, and Shopify context are a clearer paid product than a generic form builder.
- **Beats `video-engine`:** Bannerbear is a better first deliverable if “video” means repeatable template rendering. It does not replace generative video; it is much more tractable and can sell as an API.
- **Beats `analytics-engine`:** Geckoboard has a clear $79/$319 paid-tier anchor and dataset-push MVP; Baremetrics is the higher-value SaaS-specific analytics niche but more dependent on billing-data correctness.
- **No clear beat:** `schedule-engine`, `meeting-engine`, and `tts-engine` remain broader primitives with multiple product directions. Keep them as infrastructure candidates, not as ranked replacements on this evidence.

## Top 3 for the sprint backlog
1. **Back in Stock:** small Shopify-first MVP, explicit $19–$99 monthly tier ladder, and a direct merchant revenue benefit.
2. **Churn Buster:** $269+ starting price makes the paid bottleneck concrete; a Stripe-only dunning slice is bounded and easy to measure.
3. **Bannerbear:** $49+ credit-metered plans create a clear self-hosting/resale story, while image/video template rendering can be built incrementally without pretending to generate viral footage.
