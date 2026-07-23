from rest_framework.permissions import BasePermission

from apps.accounts.permissions import AccessScope, workspace_access_scope


class HasWorkspaceAccess(BasePermission):
    message = "Account is not approved for workspace access."

    def has_permission(self, request, view) -> bool:
        return workspace_access_scope(request.user) != AccessScope.NONE
