import { getServiceConfig } from "@/lib/service-url";
import { getHostedLoginTranslation } from "@/lib/zitadel";
import { JsonObject } from "@zitadel/client";
import deepmerge from "deepmerge";
import { getRequestConfig } from "next-intl/server";
import { headers } from "next/headers";
import localeMessages from "../../locales/zh.json";

export default getRequestConfig(async () => {
  const locale = "zh";
  const requestHeaders = await headers();
  const { serviceConfig } = getServiceConfig(requestHeaders);
  const organization = requestHeaders.get("x-zitadel-i18n-organization") || "";

  let translations: JsonObject = {};
  try {
    translations = (await getHostedLoginTranslation({ serviceConfig, locale, organization })) || {};
  } catch (error) {
    console.warn("获取自定义翻译失败：", error);
  }

  return {
    locale,
    // Local Chinese messages take precedence over hosted translations.
    messages: deepmerge.all([translations, localeMessages]) as Record<string, string>,
  };
});
