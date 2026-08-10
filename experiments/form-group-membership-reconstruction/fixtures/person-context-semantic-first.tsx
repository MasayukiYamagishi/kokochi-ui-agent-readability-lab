import { FormGroupMembershipFixture } from "./shared/form-group-membership-fixture";

function PersonContextSemanticFirstFixture() {
  return (
    <FormGroupMembershipFixture
      context="person"
      experimentId="form-group-membership-reconstruction"
      fixtureId="person-context-semantic-first"
      structure="semantic-first"
    />
  );
}

export default PersonContextSemanticFirstFixture;

