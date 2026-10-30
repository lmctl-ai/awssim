package ai.lmctl.awssim;

import io.github.hectorvent.floci.core.common.Digests;
import io.github.hectorvent.floci.core.common.RequestContext;
import io.github.hectorvent.floci.core.common.AwsJsonController;
import io.github.hectorvent.floci.core.common.AwsJson11Controller;
import io.github.hectorvent.floci.core.common.auth.*;
import io.github.hectorvent.floci.services.iam.*;
import io.github.hectorvent.floci.services.s3.S3Controller;
import io.quarkus.vertx.http.runtime.CurrentVertxRequest;
import jakarta.annotation.Priority;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.ws.rs.container.*;
import jakarta.ws.rs.core.*;
import jakarta.ws.rs.ext.Provider;
import java.io.ByteArrayInputStream;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Header SigV4 and identity policy boundary; upstream supplies crypto, action/ARN mapping and IAM evaluation. */
@Provider
@ApplicationScoped
@Priority(950) // After upstream account/region context (900), before other auth filters.
public class AuthFilter implements ContainerRequestFilter {
    @Context ResourceInfo resource;
    @Inject CurrentVertxRequest currentRequest;
    @Inject RequestContext requestContext;
    @Inject IamActionRegistry actions;
    @Inject ResourceArnBuilder resources;
    @Inject IamPolicyEvaluator policies;
    private static final Map<String, Set<String>> SUPPORTED = Map.of(
        "s3", Set.of("ListAllMyBuckets", "CreateBucket", "DeleteBucket", "ListBucket", "GetBucketLocation",
            "PutBucketTagging", "GetBucketTagging", "DeleteBucketTagging", "PutObject", "GetObject", "DeleteObject"),
        "dynamodb", Set.of("CreateTable", "DescribeTable", "DeleteTable", "ListTables", "PutItem", "GetItem",
            "DeleteItem", "UpdateItem", "Query", "Scan", "TagResource", "UntagResource", "ListTagsOfResource"),
        "sqs", Set.of("CreateQueue", "DeleteQueue", "GetQueueUrl", "GetQueueAttributes", "ListQueues",
            "SendMessage", "ReceiveMessage", "DeleteMessage", "TagQueue", "UntagQueue", "ListQueueTags"));
    private static final Set<String> S3_QUERY = Set.of("tagging", "location", "list-type", "prefix", "delimiter",
        "encoding-type", "continuation-token", "max-keys", "start-after");
    private static final DateTimeFormatter DATE = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'").withZone(ZoneOffset.UTC);
    public void filter(ContainerRequestContext ctx) {
        Map<String, AuthConfig.Credential> config = AuthConfig.credentials;
        if (config == null) return;
        if (ctx.getMethod().equals("GET") && Set.of("_floci/health", "/_floci/health").contains(ctx.getUriInfo().getPath())) return;
        String target = ctx.getHeaderString("X-Amz-Target");
        Class<?> handler = resource.getResourceClass();
        boolean json = handler == AwsJsonController.class || handler == AwsJson11Controller.class;
        String service = handler == S3Controller.class ? "s3"
                : json && target != null && target.startsWith("DynamoDB_20120810.") ? "dynamodb"
                : json && target != null && target.startsWith("AmazonSQS.") ? "sqs" : null;
        // Only supported, unambiguous protocols enter this boundary. Unknown routes fail closed.
        if (service == null) { reject(ctx, false, "AccessDenied", "Unsupported operation in enforced mode"); return; }
        boolean xml = service.equals("s3");
        try {
            String rawPath = currentRequest.getCurrent().request().path();
            String rawQuery = currentRequest.getCurrent().request().query();
            if (!xml && (!rawPath.equals("/") || (rawQuery != null && !rawQuery.isEmpty()))) {
                reject(ctx, false, "AccessDenied", "Unsupported JSON request route"); return;
            }
            if (xml) {
                String path = java.net.URI.create(rawPath).getPath();
                if (!Set.of("GET", "HEAD", "PUT", "DELETE").contains(ctx.getMethod())
                        || ctx.getHeaderString("x-amz-copy-source") != null || path.contains("//") || rawPath.contains("//")
                        || !rawPath.equals(ctx.getUriInfo().getRequestUri().getRawPath())
                        || List.of(path.split("/")).stream().anyMatch(p -> p.equals(".") || p.equals(".."))
                        || rawPath.toLowerCase(java.util.Locale.ROOT).contains("%2f")
                        || !S3_QUERY.containsAll(ctx.getUriInfo().getQueryParameters().keySet())
                        || ((ctx.getUriInfo().getQueryParameters().containsKey("tagging")
                             || ctx.getUriInfo().getQueryParameters().containsKey("location"))
                            && ctx.getUriInfo().getQueryParameters().size() != 1)) {
                    reject(ctx, true, "AccessDenied", "Unsupported S3 operation in enforced mode"); return;
                }
            }
            SigV4AuthorizationHeader auth = SigV4AuthorizationHeader.parse(ctx.getHeaderString("Authorization"));
            CredentialScope scope = auth == null ? null : CredentialScope.parse(auth.credential());
            if (scope == null || auth.signedHeaders() == null || auth.signature() == null
                    || !auth.signature().matches("[0-9a-f]{64}")) {
                reject(ctx, xml, "SignatureDoesNotMatch", "A valid SigV4 Authorization header is required"); return;
            }
            AuthConfig.Credential credential = config.get(scope.accessKeyId());
            if (credential == null) { reject(ctx, xml, xml ? "InvalidAccessKeyId" : "UnrecognizedClientException", "Unknown access key"); return; }
            String headers = auth.signedHeaders();
            List<String> names = List.of(headers.split(";"));
            String date = ctx.getHeaderString("X-Amz-Date");
            if (!"000000000000".equals(requestContext.getAccountId())
                    || !scope.region().equals(requestContext.getRegion())
                    || !scope.accessKeyId().equals(requestContext.getAccessKeyId())
                    || !scope.service().equals(service) || !names.contains("host") || !names.contains("x-amz-date")
                    || (target != null && !names.contains("x-amz-target"))
                    || !names.equals(names.stream().distinct().sorted().toList())
                    || names.stream().anyMatch(n -> !n.matches("[a-z0-9-]+") || ctx.getHeaderString(n) == null)
                    || date == null || !date.startsWith(scope.date())
                    || Math.abs(Instant.now().getEpochSecond() - Instant.from(DATE.parse(date)).getEpochSecond()) > 900
                    || ctx.getHeaderString("X-Amz-Security-Token") != null) throw new IllegalArgumentException();
            byte[] body = ctx.getEntityStream().readNBytes(16 * 1024 * 1024 + 1);
            if (body.length > 16 * 1024 * 1024) { reject(ctx, xml, "AccessDenied", "Enforced request body limit exceeded"); return; }
            ctx.setEntityStream(new ByteArrayInputStream(body));
            String actualHash = Digests.sha256Hex(body);
            String hash = ctx.getHeaderString("X-Amz-Content-Sha256");
            if (hash == null) hash = actualHash;
            else if (!hash.equals(actualHash) && !(xml && Set.of("UNSIGNED-PAYLOAD", "STREAMING-UNSIGNED-PAYLOAD-TRAILER",
                    "STREAMING-AWS4-HMAC-SHA256-PAYLOAD", "STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER").contains(hash))) throw new IllegalArgumentException();
            boolean valid = SigV4Canonicalization.signatureMatches(ctx.getMethod(), rawPath,
                    SigV4Canonicalization.canonicalQueryString(rawQuery, false),
                    SigV4Canonicalization.canonicalHeaders(headers, ctx::getHeaderString), headers, hash, date,
                    scope, credential.secret(), auth.signature());
            if (!valid) throw new IllegalArgumentException();
            if (hash.startsWith("STREAMING-AWS4-HMAC-SHA256-"))
                ChunkSignatures.verify(body, credential.secret(), scope, date, auth.signature(), hash.endsWith("-TRAILER"), ctx.getHeaderString("x-amz-trailer"));
            String action = xml ? actions.resolveRoute(service, ctx) : actions.resolve(service, ctx);
            if (action == null || !SUPPORTED.get(service).contains(action.substring(action.indexOf(':') + 1))) {
                reject(ctx, xml, "AccessDenied", "Unsupported action in enforced mode"); return;
            }
            if (credential.policy() != null) {
                List<String> arns = resources.buildResources(service, ctx, scope.region(), "000000000000");
                if (arns.isEmpty()) arns = List.of("*");
                for (String arn : arns) {
                    if (policies.evaluate(List.of(credential.policy()), action, arn) != IamPolicyEvaluator.Decision.ALLOW) {
                        reject(ctx, xml, xml ? "AccessDenied" : "AccessDeniedException", "Access denied by policy"); return;
                    }
                }
            }
        } catch (Exception e) { reject(ctx, xml, "SignatureDoesNotMatch", "Signature validation failed"); }
    }
    private static void reject(ContainerRequestContext ctx, boolean xml, String code, String message) {
        String body = ErrorTemplates.render(xml, code, message);
        ctx.abortWith(Response.status(403).type(xml ? "application/xml" : "application/x-amz-json-1.0")
                .header("x-amzn-errortype", code).entity(body).build());
    }
}
