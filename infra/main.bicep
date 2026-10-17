// [Channel] Webshop Experience API: a Python Function on Flex Consumption, in rg-intplat-experience-api-webshop. It calls the Process
// and System APIs through the gateway as its runtime identity (an allow-listed internal caller), and
// only the people gateway can call it (Easy Auth).

param location string = 'belgiumcentral'

@description('Application (client) id of the intplat-platform-apis Entra application (ADR 0014).')
param platformApisAppId string = 'e3337087-144a-4b2e-8faa-c802660b9dee'

var tags = {
  project: 'ai-intelligence-domain'
  component: 'experience-api-webshop'
  owner: 'channel-team'
}

resource runtime 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-intplat-experience-api-webshop-runtime'
  scope: resourceGroup('rg-intplat-identities')
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: 'appi-intplat-demo'
  scope: resourceGroup('rg-intplat-platform')
}

resource peopleGateway 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: 'apim-intplat-demo'
  scope: resourceGroup('rg-intplat-platform')
}

module function 'modules/function-app.bicep' = {
  name: 'function'
  params: {
    name: 'func-intplat-experience-api-webshop'
    location: location
    tags: tags
    runtimeIdentityId: runtime.id
    runtimeClientId: runtime.properties.clientId
    runtimePrincipalId: runtime.properties.principalId
    appInsightsConnectionString: appInsights.properties.ConnectionString
    platformApisAppId: platformApisAppId
    allowedPrincipalIds: [peopleGateway.identity.principalId]
    appSettings: {
      GATEWAY_URL: peopleGateway.properties.gatewayUrl
      PLATFORM_APIS_AUDIENCE: 'api://${platformApisAppId}'
    }
  }
}

output functionName string = function.outputs.name
output hostName string = function.outputs.defaultHostName
