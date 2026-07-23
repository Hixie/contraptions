from __future__ import annotations

ORGANIZATION_CONFIGURATION_QUERY = """
query OrganizationConfiguration($login: String!) {
  organization(login: $login) {
    id
    notificationDeliveryRestrictionEnabledSetting
    requiresTwoFactorAuthentication
    samlIdentityProvider {
      digestMethod
      idpCertificate
      issuer
      signatureMethod
      ssoUrl
    }
    pinnedItems(first: 100) {
      nodes {
        __typename
        ... on Gist {
          id
          description
          name
        }
        ... on Repository {
          id
          nameWithOwner
        }
      }
      pageInfo {
        hasNextPage
      }
    }
  }
}
"""


ORGANIZATION_IP_ALLOW_LIST_QUERY = """
query OrganizationIpAllowList($login: String!, $cursor: String) {
  organization(login: $login) {
    id
    ipAllowListEnabledSetting
    ipAllowListForInstalledAppsEnabledSetting
    ipAllowListEntries(first: 100, after: $cursor) {
      nodes {
        id
        allowListValue
        isActive
        name
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""


ORGANIZATION_DOMAINS_QUERY = """
query OrganizationDomains($login: String!, $cursor: String) {
  organization(login: $login) {
    id
    domains(first: 100, after: $cursor) {
      nodes {
        id
        domain
        isApproved
        isVerified
        isRequiredForPolicyEnforcement
        verificationToken
        tokenExpirationTime
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""


ORGANIZATION_CUSTOM_PROPERTIES_QUERY = """
query OrganizationCustomProperties($login: String!, $cursor: String) {
  organization(login: $login) {
    repositoryCustomProperties(first: 100, after: $cursor) {
      nodes {
        id
        propertyName
        regex
        requireExplicitValues
        source {
          __typename
        }
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""


REPOSITORY_CONFIGURATION_QUERY = """
query RepositoryConfiguration(
  $owner: String!
  $name: String!
  $environmentCursor: String
  $categoryCursor: String
) {
  repository(owner: $owner, name: $name) {
    id
    hasDiscussionsEnabled
    hasSponsorshipsEnabled
    issueCreationPolicy
    openGraphImageUrl
    usesCustomOpenGraphImage
    environments(first: 100, after: $environmentCursor) {
      nodes {
        id
        name
        isPinned
        pinnedPosition
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
    discussionCategories(first: 100, after: $categoryCursor) {
      nodes {
        id
        name
        slug
        description
        emoji
        isAnswerable
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""


REPOSITORY_BRANCH_PROTECTION_RULES_QUERY = """
query RepositoryBranchProtectionRules(
  $owner: String!
  $name: String!
  $cursor: String
) {
  repository(owner: $owner, name: $name) {
    id
    branchProtectionRules(first: 100, after: $cursor) {
      nodes {
        id
        pattern
        allowsDeletions
        allowsForcePushes
        blocksCreations
        dismissesStaleReviews
        isAdminEnforced
        lockAllowsFetchAndMerge
        lockBranch
        requireLastPushApproval
        requiredApprovingReviewCount
        requiredDeploymentEnvironments
        requiredStatusChecks {
          app {
            id
            slug
          }
          context
        }
        requiresApprovingReviews
        requiresCodeOwnerReviews
        requiresCommitSignatures
        requiresConversationResolution
        requiresDeployments
        requiresLinearHistory
        requiresStatusChecks
        requiresStrictStatusChecks
        restrictsPushes
        restrictsReviewDismissals
        bypassForcePushAllowances(first: 100) {
          nodes {
            actor {
              __typename
              ... on App { id slug }
              ... on Team { id slug }
              ... on User { id login }
            }
          }
          pageInfo { endCursor hasNextPage }
        }
        bypassPullRequestAllowances(first: 100) {
          nodes {
            actor {
              __typename
              ... on App { id slug }
              ... on Team { id slug }
              ... on User { id login }
            }
          }
          pageInfo { endCursor hasNextPage }
        }
        pushAllowances(first: 100) {
          nodes {
            actor {
              __typename
              ... on App { id slug }
              ... on Team { id slug }
              ... on User { id login }
            }
          }
          pageInfo { endCursor hasNextPage }
        }
        reviewDismissalAllowances(first: 100) {
          nodes {
            actor {
              __typename
              ... on App { id slug }
              ... on Team { id slug }
              ... on User { id login }
            }
          }
          pageInfo { endCursor hasNextPage }
        }
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""


BRANCH_PROTECTION_RULE_ACTORS_QUERY = """
query BranchProtectionRuleActors(
  $id: ID!
  $bypassForcePushCursor: String
  $bypassPullRequestCursor: String
  $pushCursor: String
  $reviewDismissalCursor: String
) {
  node(id: $id) {
    ... on BranchProtectionRule {
      bypassForcePushAllowances(
        first: 100
        after: $bypassForcePushCursor
      ) {
        nodes {
          actor {
            __typename
            ... on App { id slug }
            ... on Team { id slug }
            ... on User { id login }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
      bypassPullRequestAllowances(
        first: 100
        after: $bypassPullRequestCursor
      ) {
        nodes {
          actor {
            __typename
            ... on App { id slug }
            ... on Team { id slug }
            ... on User { id login }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
      pushAllowances(first: 100, after: $pushCursor) {
        nodes {
          actor {
            __typename
            ... on App { id slug }
            ... on Team { id slug }
            ... on User { id login }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
      reviewDismissalAllowances(
        first: 100
        after: $reviewDismissalCursor
      ) {
        nodes {
          actor {
            __typename
            ... on App { id slug }
            ... on Team { id slug }
            ... on User { id login }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
    }
  }
}
"""


TEAM_REVIEW_ASSIGNMENT_QUERY = """
query TeamReviewAssignment($organization: String!, $slug: String!) {
  organization(login: $organization) {
    team(slug: $slug) {
      id
      reviewRequestDelegationAlgorithm
      reviewRequestDelegationEnabled
      reviewRequestDelegationMemberCount
      reviewRequestDelegationNotifyTeam
    }
  }
}
"""


UPDATE_REPOSITORY_MUTATION = """
mutation UpdateRepositoryConfiguration($input: UpdateRepositoryInput!) {
  updateRepository(input: $input) {
    repository { id }
  }
}
"""


CREATE_BRANCH_PROTECTION_RULE_MUTATION = """
mutation CreateBranchProtectionRuleConfiguration(
  $input: CreateBranchProtectionRuleInput!
) {
  createBranchProtectionRule(input: $input) {
    branchProtectionRule { id }
  }
}
"""


UPDATE_BRANCH_PROTECTION_RULE_MUTATION = """
mutation UpdateBranchProtectionRuleConfiguration(
  $input: UpdateBranchProtectionRuleInput!
) {
  updateBranchProtectionRule(input: $input) {
    branchProtectionRule { id }
  }
}
"""


DELETE_BRANCH_PROTECTION_RULE_MUTATION = """
mutation DeleteBranchProtectionRuleConfiguration(
  $input: DeleteBranchProtectionRuleInput!
) {
  deleteBranchProtectionRule(input: $input) {
    clientMutationId
  }
}
"""


PIN_ENVIRONMENT_MUTATION = """
mutation PinEnvironmentConfiguration($input: PinEnvironmentInput!) {
  pinEnvironment(input: $input) {
    environment { id }
  }
}
"""


REORDER_ENVIRONMENT_MUTATION = """
mutation ReorderEnvironmentConfiguration($input: ReorderEnvironmentInput!) {
  reorderEnvironment(input: $input) {
    environment { id }
  }
}
"""


UPDATE_TEAM_REVIEW_ASSIGNMENT_MUTATION = """
mutation UpdateTeamReviewAssignmentConfiguration(
  $input: UpdateTeamReviewAssignmentInput!
) {
  updateTeamReviewAssignment(input: $input) {
    team { id }
  }
}
"""


UPDATE_IP_ALLOW_LIST_ENABLED_MUTATION = """
mutation UpdateIpAllowListEnabledConfiguration(
  $input: UpdateIpAllowListEnabledSettingInput!
) {
  updateIpAllowListEnabledSetting(input: $input) {
    owner { __typename }
  }
}
"""


UPDATE_IP_ALLOW_LIST_FOR_APPS_MUTATION = """
mutation UpdateIpAllowListForAppsConfiguration(
  $input: UpdateIpAllowListForInstalledAppsEnabledSettingInput!
) {
  updateIpAllowListForInstalledAppsEnabledSetting(input: $input) {
    owner { __typename }
  }
}
"""


CREATE_IP_ALLOW_LIST_ENTRY_MUTATION = """
mutation CreateIpAllowListEntryConfiguration(
  $input: CreateIpAllowListEntryInput!
) {
  createIpAllowListEntry(input: $input) {
    ipAllowListEntry { id }
  }
}
"""


UPDATE_IP_ALLOW_LIST_ENTRY_MUTATION = """
mutation UpdateIpAllowListEntryConfiguration(
  $input: UpdateIpAllowListEntryInput!
) {
  updateIpAllowListEntry(input: $input) {
    ipAllowListEntry { id }
  }
}
"""


DELETE_IP_ALLOW_LIST_ENTRY_MUTATION = """
mutation DeleteIpAllowListEntryConfiguration(
  $input: DeleteIpAllowListEntryInput!
) {
  deleteIpAllowListEntry(input: $input) {
    clientMutationId
  }
}
"""


UPDATE_NOTIFICATION_RESTRICTION_MUTATION = """
mutation UpdateNotificationRestrictionConfiguration(
  $input: UpdateNotificationRestrictionSettingInput!
) {
  updateNotificationRestrictionSetting(input: $input) {
    owner { __typename }
  }
}
"""


ADD_DOMAIN_MUTATION = """
mutation AddOrganizationDomainConfiguration($input: AddVerifiableDomainInput!) {
  addVerifiableDomain(input: $input) {
    domain { id }
  }
}
"""


DELETE_DOMAIN_MUTATION = """
mutation DeleteOrganizationDomainConfiguration(
  $input: DeleteVerifiableDomainInput!
) {
  deleteVerifiableDomain(input: $input) {
    clientMutationId
  }
}
"""


APPROVE_DOMAIN_MUTATION = """
mutation ApproveOrganizationDomainConfiguration(
  $input: ApproveVerifiableDomainInput!
) {
  approveVerifiableDomain(input: $input) {
    domain { id }
  }
}
"""


VERIFY_DOMAIN_MUTATION = """
mutation VerifyOrganizationDomainConfiguration(
  $input: VerifyVerifiableDomainInput!
) {
  verifyVerifiableDomain(input: $input) {
    domain { id }
  }
}
"""


CREATE_CUSTOM_PROPERTY_MUTATION = """
mutation CreateCustomPropertyConfiguration(
  $input: CreateRepositoryCustomPropertyInput!
) {
  createRepositoryCustomProperty(input: $input) {
    repositoryCustomProperty { id }
  }
}
"""


UPDATE_CUSTOM_PROPERTY_MUTATION = """
mutation UpdateCustomPropertyConfiguration(
  $input: UpdateRepositoryCustomPropertyInput!
) {
  updateRepositoryCustomProperty(input: $input) {
    repositoryCustomProperty { id }
  }
}
"""


DELETE_CUSTOM_PROPERTY_MUTATION = """
mutation DeleteCustomPropertyConfiguration(
  $input: DeleteRepositoryCustomPropertyInput!
) {
  deleteRepositoryCustomProperty(input: $input) {
    clientMutationId
  }
}
"""
