from buildbot.plugins import reporters


_template = u'''\
<h4>Build status: {{ summary }}</h4>
<p> Worker used: {{ workername }}</p>
{% for step in build['steps'] %}
<p> {{ step['name'] }}: {{ step['result'] }}</p>
{% endfor %}
<p><b> -- The Buildbot</b></p>
'''

class BuilderReporterMixin:

    def __init__(self, *args, builders, **kwargs):
        builder_names = [b.name for b in builders]
        super().__init__(*args, builders=builder_names, **kwargs)


class ZulipMailNotifier(reporters.MailNotifier):

    def __init__(self, zulipaddr, fromaddr, template=None):
        formatter = reporters.MessageFormatter(
            template=template or _template,
            template_type='html',
            wantProperties=True,
            wantSteps=True
        )
        super().__init__(fromaddr=fromaddr, extraRecipients=[zulipaddr],
                         messageFormatter=formatter,
                         sendToInterestedUsers=False)


class GitHubStatusPush(BuilderReporterMixin, reporters.GitHubStatusPush):
    pass


class GitHubCommentPush(BuilderReporterMixin, reporters.GitHubCommentPush):
    pass


class GitHubReviewPush(GitHubCommentPush):
    name = "GitHubReviewPush"

    def createStatus(self,
                     repo_user, repo_name, sha, state, target_url=None,
                     context=None, issue=None, description=None):
        """
        :param repo_user: GitHub user or organization
        :param repo_name: Name of the repository
        :param issue: Pull request number
        :param state: one of the following 'pending', 'success', 'error'
                      or 'failure'.
        :param description: Short description of the status.
        :return: A deferred with the result from GitHub.
        This code comes from txgithub by @tomprince.
        txgithub is based on twisted's webclient agent, which is much less reliable and featureful
        as txrequest (support for proxy, connection pool, keep alive, retry, etc)
        """

        # Convert state into the expected review status.
        review_status = {
            'success': 'APPROVE',
            'pending': 'PENDING',
            'error': 'REQUEST_CHANGES',
            'failure': 'REQUEST_CHANGES',
        }[state]

        payload = {
            'commit_id': sha,
            'event': review_status,
            'body': description
        }

        return self._http.post(
            '/'.join(['/repos', repo_user, repo_name, 'pulls', issue, 'reviews']),
            json=payload)
