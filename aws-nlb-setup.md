# Set up AWS NLB for Service LoadBalancer Multiplexer

## 1. Install AWS Load Balancer Controller

Official Instructions: [AWS Load Balancer Controller Installation Guide](https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.9/guide/installation)

### 1.1 Create IRSA

```bash
REGION=us-west-2
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
NAMESPACE=alb-controller
CLUSTER=your-cluster-name

# this might not need for our clusters
eksctl utils associate-iam-oidc-provider \
    --region $REGION \
    --cluster $CLUSTER \
    --approve

curl -o /tmp/iam-policy.json https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.11.0/docs/install/iam_policy.json

aws iam create-policy \
    --policy-name AWSLoadBalancerControllerIAMPolicy \
    --policy-document file:///tmp/iam-policy.json

ROLE_NAME=AWSLoadBalancerControllerIAMRole-$CLUSTER

eksctl create iamserviceaccount \
    --cluster=$CLUSTER \
    --namespace=$NAMESPACE \
    --name=aws-load-balancer-controller \
    --role-name=$ROLE_NAME \
    --role-only \
    --attach-policy-arn=arn:aws:iam::$AWS_ACCOUNT_ID:policy/AWSLoadBalancerControllerIAMPolicy \
    --region $REGION \
    --approve

# get the role name
ROLE_ARN=arn:aws:iam::${AWS_ACCOUNT_ID}:role/$ROLE_NAME
kubectl create ns $NAMESPACE
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: aws-load-balancer-controller
  namespace: $NAMESPACE
  annotations:
    eks.amazonaws.com/role-arn: $ROLE_ARN
EOF
```

### 1.2 Install AWS Load Balancer Controller

```bash
helm repo add eks https://aws.github.io/eks-charts

# install helm chart
# Due to issue https://github.com/kubernetes-sigs/aws-load-balancer-controller/issues/3913
# We need to use our own patched image before the issue is fixed.
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
    -n $NAMESPACE \
    --set clusterName=$CLUSTER \
    --set serviceAccount.create=false \
    --set serviceAccount.name=aws-load-balancer-controller
```

### 1.3 Set Tag to Subnets

To make sure the AWS Load Balancer Controller can find the subnets, we need to tag the subnets with

- `kubernetes.io/cluster/<cluster-name>` = `shared|owned`
- `kubernetes.io/role/elb` = `1`

> For EKS cluster created by eksctl, can skip this step.

```bash
export AWS_REGION=us-west-2

# all AZs in the region
# AZS=$(aws ec2 describe-availability-zones --query "AvailabilityZones[*].ZoneName" --output text | tr '\t' ',')

# only tag the subnets in specific AZs
AZS=us-west-2a
# get the subnets of cluster
SUBNETS=$(
    aws ec2 describe-subnets \
    --filters "Name=availability-zone,Values=$AZS" \
              "Name=tag:kubernetes.io/cluster/$CLUSTER,Values=shared,owned" \
    --query "Subnets[*].SubnetId" --output text | tr '\t' ' '
)

# tag the subnets
aws ec2 create-tags --resources $(echo $SUBNETS) --tags Key=kubernetes.io/role/elb,Value=1
```

## 2. Set up NLB for Service LoadBalancer Multiplexer

The mux Service needs the following settings

```yaml
annotations:
    # https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.9/guide/service/nlb
    service.beta.kubernetes.io/aws-load-balancer-type: external
    # create a internet-facing NLB
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing
    # use pod ip as target (need AWS VPC CNI enabled)
    service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip
# tell the AWS Load Balancer Controller to create a NLB
loadBalancerClass: service.k8s.aws/nlb
allocateLoadBalancerNodePorts: false
```

## 3. Known Issues

> [!IMPORTANT]
> Due to some unknown problem, the NLB might not be able to access the pods. And all endpoints will be unhealthy.
> To solve this, you can find the security group of the NLB and add the security group to the cluster nodes security group
> allowing all traffic from the NLB security group.
